"""Metadata service: knows which objects exist, which chunks they are made of,
and which storage nodes hold each chunk.

Write path: the uploaded file is cut into chunks, each chunk is named by its
SHA-256 hash, and a chunk is only sent to the storage nodes if we have never seen
it before (deduplication). New chunks go to the nodes chosen by the hashing ring.

Read path: the chunk list of the object is read from PostgreSQL and each chunk is
fetched from one of its nodes, checking the hash before sending it on.
"""
import hashlib
import os
import tempfile
import threading
import time

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from . import db
from .chunking import chunk
from .ring import HashRing

REPLICAS = int(os.environ.get("REPLICAS", "3"))
NODE_TIMEOUT_SECONDS = float(os.environ.get("NODE_TIMEOUT_SECONDS", "10"))
READ_BLOCK = 1024 * 1024

app = FastAPI(title="metadata service")
nodes = {}            # node id -> last heartbeat (url, free space, chunk count, time)
nodes_lock = threading.Lock()
http = requests.Session()


def log(message):
    print(f"{time.strftime('%H:%M:%S')} [metadata] {message}", flush=True)


@app.on_event("startup")
def startup():
    for attempt in range(30):  # PostgreSQL may still be starting
        try:
            db.init()
            log("connected to PostgreSQL")
            return
        except Exception as error:
            log(f"waiting for PostgreSQL ({type(error).__name__})")
            time.sleep(1)
    raise RuntimeError("PostgreSQL is not reachable")


# ---------- storage nodes ----------

@app.post("/heartbeat")
def heartbeat(beat: dict):
    with nodes_lock:
        is_new = beat["node_id"] not in nodes or not alive(nodes[beat["node_id"]])
        nodes[beat["node_id"]] = {**beat, "last_seen": time.time()}
    if is_new:
        log(f"node {beat['node_id']} is UP at {beat['url']}")
    return {"ok": True}


def alive(info):
    return time.time() - info["last_seen"] < NODE_TIMEOUT_SECONDS


def live_nodes():
    with nodes_lock:
        return {n: info for n, info in nodes.items() if alive(info)}


def url_of(node_id):
    with nodes_lock:
        return nodes[node_id]["url"] if node_id in nodes else None


@app.get("/nodes")
def list_nodes():
    with nodes_lock:
        return [{**info, "alive": alive(info), "seconds_since_heartbeat": round(time.time() - info["last_seen"], 1)}
                for info in sorted(nodes.values(), key=lambda i: i["node_id"])]


def current_ring():
    return HashRing(sorted(live_nodes()))


# ---------- write path ----------

def store_chunk(data, chunk_hash, ring):
    """Send a new chunk to its target nodes. Returns the nodes that stored it."""
    stored = []
    for node in ring.lookup(chunk_hash, REPLICAS):
        try:
            http.put(f"{url_of(node)}/chunks/{chunk_hash}", data=data, timeout=30).raise_for_status()
            stored.append(node)
        except requests.RequestException as error:
            log(f"could not store {chunk_hash[:12]} on {node}: {type(error).__name__}")
    if not stored:
        raise HTTPException(503, "no storage node accepted the chunk")
    return stored


def store_object(path, bucket, key, method):
    ring = current_ring()
    if not ring.nodes:
        raise HTTPException(503, "no storage nodes are alive")
    started = time.perf_counter()
    hashes, size, new_chunks, reused = [], 0, 0, 0

    def blocks():
        with open(path, "rb") as f:
            while block := f.read(READ_BLOCK):
                yield block

    with db.connect() as conn:
        for data in chunk(blocks(), method):
            chunk_hash = hashlib.sha256(data).hexdigest()
            if db.chunk_nodes(conn, chunk_hash) is not None:
                reused += 1                        # already stored: only count the reference
                db.add_reference(conn, chunk_hash, len(data), [])
            else:
                placed = store_chunk(data, chunk_hash, ring)
                db.add_reference(conn, chunk_hash, len(data), placed)
                new_chunks += 1
            hashes.append(chunk_hash)
            size += len(data)
        version = db.next_version(conn, bucket, key)
        db.save_object(conn, bucket, key, version, size, method, hashes)
    seconds = time.perf_counter() - started
    log(f"PUT {bucket}/{key} v{version}: {size} bytes, {len(hashes)} chunks ({new_chunks} new, {reused} deduplicated), {seconds:.2f}s")
    return {"bucket": bucket, "key": key, "version": version, "size": size, "chunking": method,
            "chunks": len(hashes), "new_chunks": new_chunks, "deduplicated_chunks": reused, "seconds": round(seconds, 3)}


@app.put("/objects/{bucket}/{key:path}", status_code=201)
async def put_object(bucket: str, key: str, request: Request, chunking: str = "fixed"):
    if chunking not in ("fixed", "cdc"):
        raise HTTPException(400, "chunking must be fixed or cdc")
    # Save the upload to a temporary file first, so large files never sit in memory.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        async for block in request.stream():
            tmp.write(block)
    try:
        return await run_in_threadpool(store_object, tmp.name, bucket, key, chunking)
    finally:
        os.unlink(tmp.name)


# ---------- read path ----------

def read_chunk(chunk_hash, node_ids):
    """Try each node that holds the chunk until one returns the right content."""
    for node in node_ids:
        url = url_of(node)
        if not url:
            continue
        try:
            response = http.get(f"{url}/chunks/{chunk_hash}", timeout=30)
            if response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == chunk_hash:
                return response.content
            log(f"{node} could not serve {chunk_hash[:12]} (HTTP {response.status_code}), trying the next copy")
        except requests.RequestException:
            log(f"{node} is unreachable for {chunk_hash[:12]}, trying the next copy")
    raise HTTPException(503, f"no healthy copy of chunk {chunk_hash[:12]} is available")


@app.get("/objects/{bucket}/{key:path}")
def get_object(bucket: str, key: str, version: int | None = None):
    with db.connect() as conn:
        obj = db.get_object(conn, bucket, key, version)
        if not obj:
            raise HTTPException(404, "object not found")
        locations = {h: db.chunk_nodes(conn, h) for h in set(obj["chunk_hashes"])}

    def stream():
        for chunk_hash in obj["chunk_hashes"]:
            yield read_chunk(chunk_hash, locations[chunk_hash] or [])

    headers = {"X-Object-Version": str(obj["version"]), "Content-Length": str(obj["size"])}
    return StreamingResponse(stream(), media_type="application/octet-stream", headers=headers)


# ---------- delete, list, stats ----------

@app.delete("/objects/{bucket}/{key:path}")
def delete_object(bucket: str, key: str):
    freed = 0
    with db.connect() as conn:
        rows = conn.execute("SELECT chunk_hashes FROM objects WHERE bucket=%s AND key=%s", (bucket, key)).fetchall()
        if not rows:
            raise HTTPException(404, "object not found")
        to_delete = []
        for (hashes,) in rows:
            for chunk_hash in hashes:
                holders = db.remove_reference(conn, chunk_hash)
                if holders is not None:
                    to_delete.append((chunk_hash, holders))
        conn.execute("DELETE FROM objects WHERE bucket=%s AND key=%s", (bucket, key))
    for chunk_hash, holders in to_delete:   # only chunks no object uses anymore
        for node in holders:
            try:
                http.delete(f"{url_of(node)}/chunks/{chunk_hash}", timeout=10)
            except (requests.RequestException, TypeError):
                pass
        freed += 1
    log(f"DELETE {bucket}/{key}: {len(rows)} version(s), {freed} chunks no longer used and removed from the nodes")
    return {"deleted_versions": len(rows), "chunks_removed": freed}


@app.get("/objects/{bucket}")
def list_objects(bucket: str, prefix: str = ""):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (key) key, version, size, chunking, created_at FROM objects
               WHERE bucket = %s AND key LIKE %s ORDER BY key, version DESC""",
            (bucket, prefix.replace("%", r"\%") + "%"),
        ).fetchall()
    return [dict(zip(["key", "version", "size", "chunking", "created_at"], r)) for r in rows]


@app.get("/stats")
def stats():
    with db.connect() as conn:
        objects, logical = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM objects").fetchone()
        chunks, unique_bytes, refs = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0), COALESCE(SUM(refcount), 0) FROM chunks").fetchone()
    return {
        "objects": objects, "logical_bytes": logical,
        "unique_chunks": chunks, "chunk_references": refs, "unique_bytes": unique_bytes,
        "dedup_ratio": round(logical / unique_bytes, 3) if unique_bytes else None,
        "replicas": REPLICAS, "live_nodes": sorted(live_nodes()),
    }


@app.get("/chunk-hashes")
def chunk_hashes():
    """Every chunk hash with its nodes (used by the placement test)."""
    with db.connect() as conn:
        return {h: n for h, n in conn.execute("SELECT hash, nodes FROM chunks").fetchall()}
