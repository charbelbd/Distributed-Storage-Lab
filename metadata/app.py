"""Metadata service: knows the buckets, the objects, which chunks each object is
made of, and where every chunk (or erasure-coded piece) is stored.

Write path: the uploaded file is cut into chunks and each chunk is named by its
    SHA-256 hash. A chunk we already have only gets one more reference (deduplication).
    A new chunk is protected the way its bucket asks: N copies, or k + m pieces,
    on the nodes chosen by the consistent hashing ring.
Read path: the chunk list comes from PostgreSQL; each chunk is read from its nodes
    (quorum read, or k pieces decoded) and checked against its hash.
Repair: a background worker notices chunks that lost copies or pieces on a dead
    node and rebuilds them on healthy nodes.

Only the gateway talks to this service; users go through the gateway.
"""
import os
import re
import tempfile
import threading
import time

import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from . import db, redundancy, search_index
from .chunking import chunk
from .redundancy import QuorumError, sha
from .ring import HashRing

DEFAULTS = {
    "replicas": int(os.environ.get("REPLICAS", "3")),
    "write_quorum": int(os.environ.get("WRITE_QUORUM", "2")),
    "read_quorum": int(os.environ.get("READ_QUORUM", "2")),
    "k": int(os.environ.get("EC_K", "4")),
    "m": int(os.environ.get("EC_M", "2")),
}
NODE_TIMEOUT = float(os.environ.get("NODE_TIMEOUT_SECONDS", "10"))     # no heartbeat for this long = down
REPAIR_AFTER = float(os.environ.get("REPAIR_AFTER_SECONDS", "20"))     # down for this long = rebuild its data
REPAIR_INTERVAL = float(os.environ.get("REPAIR_INTERVAL_SECONDS", "5"))
SCRUB_INTERVAL = float(os.environ.get("SCRUB_INTERVAL_SECONDS", "300"))  # every 5 minutes
ORPHAN_GRACE = float(os.environ.get("ORPHAN_GRACE_SECONDS", "600"))     # never delete a copy younger than this
READ_BLOCK = 1024 * 1024

app = FastAPI(title="metadata service")
nodes = {}                      # node id -> last heartbeat (url, free space, chunk count, time)
nodes_lock = threading.RLock()     # re-entrant: is_alive() is also called while holding it
started_at = time.time()
reported_failures = {}          # chunk -> last repair error, so the same failure is logged once
repair = {"running": False, "to_repair": 0, "repaired": 0, "failed": 0, "lost": 0,
          "total_repaired": 0, "last_run": None, "last_duration_s": None}
scrub_state = {"running": False, "last_run": None, "duration_s": None, "nodes_checked": 0, "copies_checked": 0,
               "bytes_checked": 0, "corrupted": 0, "missing": 0, "orphans_removed": 0, "runs": 0}
maintenance_lock = threading.Lock()   # repair and scrub never run at the same time


def log(message):
    print(f"{time.strftime('%H:%M:%S')} [metadata] {message}", flush=True)


@app.on_event("startup")
def startup():
    for _ in range(60):  # PostgreSQL may still be starting
        try:
            db.init()
            log("connected to PostgreSQL")
            break
        except Exception as error:
            log(f"waiting for PostgreSQL ({type(error).__name__})")
            time.sleep(1)
    else:
        raise RuntimeError("PostgreSQL is not reachable")
    search_index.setup(log)
    threading.Thread(target=repair_loop, daemon=True).start()


# ---------- storage nodes ----------

@app.post("/heartbeat")
def heartbeat(beat: dict):
    with nodes_lock:
        was_up = beat["node_id"] in nodes and is_alive(beat["node_id"])
        nodes[beat["node_id"]] = {**beat, "last_seen": time.time()}
    if not was_up:
        log(f"node {beat['node_id']} is UP at {beat['url']}")
    return {"ok": True}


def seconds_silent(node_id):
    with nodes_lock:
        return time.time() - nodes[node_id]["last_seen"] if node_id in nodes else float("inf")


def is_alive(node_id):
    return seconds_silent(node_id) < NODE_TIMEOUT


def live_nodes():
    with nodes_lock:
        ids = list(nodes)
    return sorted(n for n in ids if is_alive(n))


def url_of(node_id):
    with nodes_lock:
        return nodes[node_id]["url"] if node_id in nodes else None


def alive_first(holders):
    return sorted([n for n in holders if n], key=lambda n: not is_alive(n))


def ring():
    return HashRing(live_nodes())


# ---------- buckets ----------

@app.post("/buckets", status_code=201)
def create_bucket(body: dict):
    name = body.get("name", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", name):
        raise HTTPException(400, "bucket name: 3-63 characters, lowercase letters, digits, dots and dashes")
    bucket = {"name": name, "redundancy": body.get("redundancy", "replication"), **DEFAULTS}
    for field in DEFAULTS:
        if field in body:
            bucket[field] = int(body[field])
    if bucket["redundancy"] not in ("replication", "ec"):
        raise HTTPException(400, "redundancy must be replication or ec")
    n, w, r = bucket["replicas"], bucket["write_quorum"], bucket["read_quorum"]
    if bucket["redundancy"] == "replication" and not (1 <= w <= n and 1 <= r <= n and w + r > n):
        raise HTTPException(400, f"quorums must satisfy W + R > N (got N={n}, W={w}, R={r})")
    if bucket["redundancy"] == "ec" and not (bucket["k"] >= 1 and bucket["m"] >= 1):
        raise HTTPException(400, "k and m must be at least 1")
    with db.connect() as conn:
        if db.get_bucket(conn, name):
            raise HTTPException(409, "bucket already exists")
        db.create_bucket(conn, bucket)
        created = db.get_bucket(conn, name)
    log(f"bucket {name} created ({describe(created)})")
    return created


def describe(b):
    if b["redundancy"] == "ec":
        return f"erasure coding {b['k']}+{b['m']}"
    return f"replication N={b['replicas']} W={b['write_quorum']} R={b['read_quorum']}"


@app.get("/buckets")
def list_buckets():
    with db.connect() as conn:
        return db.list_buckets(conn)


def bucket_or_404(conn, name):
    bucket = db.get_bucket(conn, name)
    if not bucket:
        raise HTTPException(404, f"bucket {name} does not exist")
    return bucket


# ---------- write path ----------

def chunk_key(content_hash, bucket):
    """Deduplication works within one protection scheme: the same content uploaded to a
    replicated bucket and to an erasure-coded bucket is protected both ways, as each bucket asks."""
    if bucket["redundancy"] == "ec":
        return f"{content_hash}-ec{bucket['k']}+{bucket['m']}"
    return content_hash


def protect_chunk(data, chunk_hash, bucket):
    """Store a new chunk on the nodes, the way its bucket asks."""
    placement = ring()
    if bucket["redundancy"] == "ec":
        k, m = bucket["k"], bucket["m"]
        targets = placement.lookup(chunk_hash, k + m)   # the ring only uses the first hex digits
        if len(targets) < k + m:
            raise QuorumError(f"erasure coding {k}+{m} needs {k + m} live nodes, only {len(targets)} are up")
        pieces, holders, padding = redundancy.write_ec(data, targets, url_of, k, m)
        return {"scheme": "ec", "nodes": holders, "pieces": pieces, "k": k, "m": m, "padding": padding, "target": k + m}
    targets = placement.lookup(chunk_hash, bucket["replicas"])
    holders = redundancy.write_replicated(data, chunk_hash, targets, url_of, bucket["write_quorum"])
    return {"scheme": "replication", "nodes": holders, "pieces": [], "k": 0, "m": 0, "padding": 0, "target": bucket["replicas"]}


def store_object(path, bucket_name, key, method, tags):
    started = time.perf_counter()
    hashes, size, new_chunks, reused = [], 0, 0, 0

    def blocks():
        with open(path, "rb") as f:
            while block := f.read(READ_BLOCK):
                yield block

    with db.connect() as conn:
        bucket = bucket_or_404(conn, bucket_name)
        if not live_nodes():
            raise HTTPException(503, "no storage nodes are alive")
        for data in chunk(blocks(), method):
            chunk_hash = chunk_key(sha(data), bucket)
            if db.get_chunk(conn, chunk_hash):
                db.add_reference(conn, chunk_hash)        # already stored: one more reference
                reused += 1
            else:
                try:
                    stored = protect_chunk(data, chunk_hash, bucket)
                except QuorumError as error:
                    log(f"PUT {bucket_name}/{key} failed: {error}")
                    raise HTTPException(503, str(error))
                db.add_chunk(conn, {"hash": chunk_hash, "size": len(data), **stored})
                new_chunks += 1
            hashes.append(chunk_hash)
            size += len(data)
        version = db.next_version(conn, bucket_name, key)
        db.save_object(conn, bucket_name, key, version, size, method, hashes, tags)
        obj = db.get_object(conn, bucket_name, key, version)
    seconds = time.perf_counter() - started
    log(f"PUT {bucket_name}/{key} v{version}: {size} bytes, {len(hashes)} chunks "
        f"({new_chunks} new, {reused} deduplicated), {describe(bucket)}, {seconds:.2f}s")
    search_index.index(obj)
    return {"bucket": bucket_name, "key": key, "version": version, "size": size, "chunking": method,
            "redundancy": describe(bucket), "chunks": len(hashes), "new_chunks": new_chunks,
            "deduplicated_chunks": reused, "tags": tags, "seconds": round(seconds, 3)}


@app.put("/buckets/{bucket}/objects/{key:path}", status_code=201)
async def put_object(bucket: str, key: str, request: Request, chunking: str = "fixed"):
    if chunking not in ("fixed", "cdc"):
        raise HTTPException(400, "chunking must be fixed or cdc")
    # User-defined tags come as headers:  X-Tag-Project: thesis  ->  {"project": "thesis"}
    tags = {k[6:].lower(): v for k, v in request.headers.items() if k.lower().startswith("x-tag-")}
    # Save the upload to a temporary file first, so large files never sit in memory.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        async for block in request.stream():
            tmp.write(block)
    try:
        return await run_in_threadpool(store_object, tmp.name, bucket, key, chunking, tags)
    finally:
        os.unlink(tmp.name)


# ---------- read path ----------

def read_chunk(c, read_quorum):
    if c["scheme"] == "ec":
        return redundancy.read_ec(c["hash"][:64], c["pieces"], c["nodes"], url_of, c["k"], c["m"], c["padding"])
    holders = alive_first(c["nodes"])
    return redundancy.read_replicated(c["hash"], holders, url_of, min(read_quorum, len(holders)) or 1)


def parse_range(header, size):
    """'bytes=100-199' -> (100, 199). Also 'bytes=100-' and 'bytes=-50' (the last 50 bytes)."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or match.groups() == ("", ""):
        raise HTTPException(416, "unsupported Range header")
    start, end = match.groups()
    if start == "":
        start, end = max(size - int(end), 0), size - 1
    else:
        start, end = int(start), min(int(end), size - 1) if end else size - 1
    if start >= size or start > end:
        raise HTTPException(416, f"range not satisfiable (object size {size})")
    return start, end


def load_object(bucket, key, version):
    with db.connect() as conn:
        b = bucket_or_404(conn, bucket)
        obj = db.get_object(conn, bucket, key, version)
        if not obj:
            raise HTTPException(404, "object not found")
        chunks = {h: db.get_chunk(conn, h) for h in set(obj["chunk_hashes"])}
    return b, obj, chunks


@app.get("/buckets/{bucket}/objects/{key:path}")
def get_object(bucket: str, key: str, request: Request, version: int | None = None):
    b, obj, chunks = load_object(bucket, key, version)
    size = obj["size"]
    start, end, status = 0, size - 1, 200
    if request.headers.get("range") and size:
        start, end = parse_range(request.headers["range"], size)
        status = 206

    def stream():
        offset = 0
        for chunk_hash in obj["chunk_hashes"]:
            c = chunks[chunk_hash]
            chunk_start, offset = offset, offset + c["size"]
            if offset <= start or chunk_start > end:
                continue                       # this chunk is outside the requested range
            try:
                data = read_chunk(c, b["read_quorum"])
            except QuorumError as error:
                log(f"GET {bucket}/{key}: {error}")
                raise
            yield data[max(start - chunk_start, 0): end - chunk_start + 1]

    # Read the first needed chunk before answering, so a failure becomes a clean 503.
    body = stream()
    try:
        first = next(body, b"")
    except QuorumError as error:
        raise HTTPException(503, str(error))

    def everything():
        yield first
        yield from body

    headers = {"X-Object-Version": str(obj["version"]), "Accept-Ranges": "bytes",
               "Content-Length": str(end - start + 1 if size else 0)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(everything(), status_code=status, media_type="application/octet-stream", headers=headers)


@app.head("/buckets/{bucket}/objects/{key:path}")
def head_object(bucket: str, key: str, version: int | None = None):
    _, obj, _ = load_object(bucket, key, version)
    return Response(headers={"Content-Length": str(obj["size"]), "X-Object-Version": str(obj["version"]),
                             "Accept-Ranges": "bytes", "Last-Modified": obj["created_at"].strftime("%a, %d %b %Y %H:%M:%S GMT")})


@app.get("/meta/{bucket}/{key:path}")
def object_metadata(bucket: str, key: str, version: int | None = None):
    b, obj, chunks = load_object(bucket, key, version)
    with db.connect() as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM objects WHERE bucket=%s AND key=%s ORDER BY version",
                                               (bucket, key)).fetchall()]
    return {**obj, "versions": versions, "redundancy": describe(b),
            "chunks": [{"hash": h, "size": chunks[h]["size"], "scheme": chunks[h]["scheme"], "nodes": chunks[h]["nodes"],
                        "healthy_copies": healthy_copies(chunks[h]), "target": chunks[h]["target"]}
                       for h in obj["chunk_hashes"]]}


# ---------- delete, list, search ----------

def delete_from_nodes(c):
    targets = list(zip(c["pieces"], c["nodes"])) if c["scheme"] == "ec" else [(c["hash"], n) for n in c["nodes"]]
    for piece_hash, node in targets:
        if node and url_of(node):
            try:
                redundancy.http.delete(f"{url_of(node)}/chunks/{piece_hash}", timeout=10)
            except requests.RequestException:
                pass   # node down: the copy stays behind until the scrubber cleans it


@app.delete("/buckets/{bucket}/objects/{key:path}")
def delete_object(bucket: str, key: str):
    unused = []
    with db.connect() as conn:
        bucket_or_404(conn, bucket)
        rows = conn.execute("SELECT chunk_hashes FROM objects WHERE bucket=%s AND key=%s", (bucket, key)).fetchall()
        if not rows:
            raise HTTPException(404, "object not found")
        for (hashes,) in rows:
            for chunk_hash in hashes:
                c = db.remove_reference(conn, chunk_hash)
                if c:
                    unused.append(c)
        conn.execute("DELETE FROM objects WHERE bucket=%s AND key=%s", (bucket, key))
    for c in unused:          # only chunks that no object uses anymore
        delete_from_nodes(c)
    search_index.remove(bucket, key)
    log(f"DELETE {bucket}/{key}: {len(rows)} version(s), {len(unused)} chunks no longer used and removed from the nodes")
    return {"deleted_versions": len(rows), "chunks_removed": len(unused)}


@app.get("/buckets/{bucket}/objects")
def list_objects(bucket: str, prefix: str = "", limit: int = 1000):
    with db.connect() as conn:
        bucket_or_404(conn, bucket)
        return [summary(o) for o in db.search_objects(conn, bucket=bucket, prefix=prefix, limit=limit)]


def summary(o):
    return {k: v for k, v in o.items() if k != "chunk_hashes"} | {"chunks": len(o["chunk_hashes"])}


@app.get("/search")
def search(bucket: str | None = None, prefix: str = "", min_size: int | None = None, max_size: int | None = None,
           after: str | None = None, before: str | None = None, tag: list[str] | None = Query(None), limit: int = 100):
    """Search in PostgreSQL (the gateway uses Elasticsearch first and falls back to this)."""
    tags = dict(t.split(":", 1) for t in (tag or []) if ":" in t)
    with db.connect() as conn:
        found = db.search_objects(conn, bucket, prefix, min_size, max_size, after, before, tags, limit)
    return {"engine": "postgresql", "count": len(found), "results": [summary(o) for o in found]}




# ---------- cluster status ----------

def healthy_copies(c):
    holders = [n for n in c["nodes"] if n]
    return sum(1 for n in holders if is_alive(n))


@app.get("/status")
def status():
    with db.connect() as conn:
        chunks = db.all_chunks(conn)
        objects, logical = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM objects").fetchone()
        degraded = [c["hash"] for c in chunks if healthy_copies(c) < c["target"]]
        degraded_objects = conn.execute(
            "SELECT DISTINCT bucket, key FROM objects WHERE chunk_hashes && %s ORDER BY bucket, key LIMIT 50",
            (degraded,)).fetchall() if degraded else []
        degraded_count = conn.execute(
            "SELECT COUNT(DISTINCT (bucket, key)) FROM objects WHERE chunk_hashes && %s", (degraded,)).fetchone()[0] if degraded else 0
    per_node, physical = {}, 0
    for c in chunks:
        piece_size = -(-c["size"] // c["k"]) if c["scheme"] == "ec" else c["size"]
        for n in c["nodes"]:
            if n:
                per_node[n] = per_node.get(n, 0) + 1
                physical += piece_size
    lost = [c["hash"] for c in chunks if healthy_copies(c) < (c["k"] if c["scheme"] == "ec" else 1)]
    with nodes_lock:
        known = sorted(nodes.values(), key=lambda i: i["node_id"])
    unique = sum(c["size"] for c in chunks)
    return {
        "nodes": [{"node_id": i["node_id"], "url": i["url"], "alive": is_alive(i["node_id"]),
                   "seconds_since_heartbeat": round(time.time() - i["last_seen"], 1),
                   "free_bytes": i["free_bytes"], "chunk_count": i["chunk_count"], "used_bytes": i.get("used_bytes", 0),
                   "chunks_assigned": per_node.get(i["node_id"], 0)} for i in known],
        "objects": objects, "logical_bytes": logical, "unique_chunks": len(chunks),
        "unique_bytes": unique, "stored_bytes": physical,
        "dedup_ratio": round(logical / unique, 3) if unique else None,
        "degraded_chunks": len(degraded), "degraded_objects": degraded_count,
        "degraded_object_list": [f"{b}/{k}" for b, k in degraded_objects],
        "lost_chunks": len(lost), "repair": repair, "scrub": scrub_state,
    }


@app.get("/chunk-hashes")
def chunk_hashes():
    """Every chunk hash with its nodes (used by the placement test)."""
    with db.connect() as conn:
        return {h: n for h, n in conn.execute("SELECT hash, nodes FROM chunks").fetchall()}


# ---------- repair ----------

def failed(node_id):
    return not node_id or seconds_silent(node_id) > REPAIR_AFTER


def repair_chunk(c):
    """Rebuild missing copies or pieces of one chunk on healthy nodes. Returns the new node list."""
    live = live_nodes()
    if c["scheme"] == "ec":
        k, m = c["k"], c["m"]
        missing = [i for i, n in enumerate(c["nodes"]) if failed(n)]
        found = redundancy.read_pieces(c["pieces"], c["nodes"], url_of, k, skip=set(missing))
        if len(found) < k:
            raise QuorumError(f"only {len(found)} pieces left, need {k}")
        data = redundancy.decode(found, k, m, c["padding"])
        pieces, _ = redundancy.encode(data, k, m)
        new_nodes = list(c["nodes"])
        free = [n for n in HashRing(live).lookup(c["hash"], len(live)) if n not in new_nodes]
        for i in missing:
            if not free:
                raise QuorumError("not enough healthy nodes to place the rebuilt piece")
            target = free.pop(0)
            if not redundancy.put(url_of(target), c["pieces"][i], pieces[i]):
                raise QuorumError(f"could not store piece {i} on {target}")
            new_nodes[i] = target
        return new_nodes
    good = [n for n in c["nodes"] if not failed(n)]
    if not good:
        raise QuorumError("no copy left")
    data = redundancy.read_replicated(c["hash"], alive_first(good), url_of, 1)
    candidates = [n for n in HashRing(live).lookup(c["hash"], len(live)) if n not in good]
    new = []
    for target in candidates[: c["target"] - len(good)]:
        if redundancy.put(url_of(target), c["hash"], data):
            new.append(target)
    if not new:
        raise QuorumError("no healthy node accepted a new copy")
    return good + new


def run_repair():
    with db.connect() as conn:
        todo = [c for c in db.all_chunks(conn) if any(failed(n) for n in c["nodes"]) or len(c["nodes"]) < c["target"]]
    if not todo:
        return
    started = time.perf_counter()
    repair.update(running=True, to_repair=len(todo), repaired=0, failed=0, lost=0)
    quiet = all(c["hash"] in reported_failures for c in todo)   # nothing new since the last attempt
    if not quiet:
        log(f"REPAIR started: {len(todo)} chunks lost copies or pieces")
    for c in todo:
        try:
            new_nodes = repair_chunk(c)
            with db.connect() as conn:
                db.set_chunk_nodes(conn, c["hash"], new_nodes)
            repair["repaired"] += 1
            repair["total_repaired"] += 1
            reported_failures.pop(c["hash"], None)
        except (QuorumError, requests.RequestException) as error:
            repair["failed"] += 1
            if "left" in str(error):
                repair["lost"] += 1
            if reported_failures.get(c["hash"]) != str(error):
                reported_failures[c["hash"]] = str(error)
                log(f"REPAIR {c['hash'][:12]} failed: {error}")
    repair.update(running=False, last_run=time.strftime("%Y-%m-%d %H:%M:%S"),
                  last_duration_s=round(time.perf_counter() - started, 2))
    if not quiet or repair["repaired"]:
        log(f"REPAIR finished: {repair['repaired']} repaired, {repair['failed']} failed, in {repair['last_duration_s']}s")


# ---------- scrubbing ----------

def run_scrub(orphan_grace=ORPHAN_GRACE):
    """Ask every live node to re-check all its chunks, then fix what it found:

    - a copy or piece that is corrupted, or missing from the node, is taken off the
      chunk's node list, so the repair worker rebuilds it on a healthy node;
    - a copy the node holds but no chunk points to (left by a crash during an upload,
      or by a node that came back after its data was repaired elsewhere) is deleted,
      if it is older than `orphan_grace` seconds, so a running upload is never touched.
    """
    started = time.perf_counter()
    scrub_state.update(running=True)
    with db.connect() as conn:
        chunks = db.all_chunks(conn)
    expected = {}   # node -> {piece hash: (chunk, index in its node list)}
    for c in chunks:
        names = c["pieces"] if c["scheme"] == "ec" else [c["hash"]] * len(c["nodes"])
        for i, (name, node) in enumerate(zip(names, c["nodes"])):
            if node:
                expected.setdefault(node, {})[name] = (c, i)
    found = {"nodes_checked": 0, "copies_checked": 0, "bytes_checked": 0, "corrupted": 0, "missing": 0, "orphans_removed": 0}
    lost_on = {}    # chunk hash -> (chunk, set of node-list indexes to clear)
    for node in live_nodes():
        try:
            report = redundancy.http.post(f"{url_of(node)}/scrub", timeout=3600).json()
        except requests.RequestException as error:
            log(f"SCRUB {node} unreachable: {type(error).__name__}")
            continue
        found["nodes_checked"] += 1
        found["copies_checked"] += len(report["good"]) + len(report["corrupted"])
        found["bytes_checked"] += report["checked_bytes"]
        found["corrupted"] += len(report["corrupted"])
        for name, (c, i) in expected.get(node, {}).items():
            if name not in report["good"]:
                if name not in report["corrupted"]:
                    found["missing"] += 1
                lost_on.setdefault(c["hash"], (c, set()))[1].add(i)
        for name, age in report["good"].items():
            if name not in expected.get(node, {}) and age >= orphan_grace:
                redundancy.http.delete(f"{url_of(node)}/chunks/{name}", timeout=30)
                found["orphans_removed"] += 1
    with db.connect() as conn:
        for chunk_hash, (c, indexes) in lost_on.items():
            if c["scheme"] == "ec":
                new_nodes = ["" if i in indexes else n for i, n in enumerate(c["nodes"])]
            else:
                new_nodes = [n for i, n in enumerate(c["nodes"]) if i not in indexes]
            db.set_chunk_nodes(conn, chunk_hash, new_nodes)
    seconds = round(time.perf_counter() - started, 2)
    scrub_state.update(running=False, last_run=time.strftime("%Y-%m-%d %H:%M:%S"), duration_s=seconds,
                       runs=scrub_state["runs"] + 1, **found)
    log(f"SCRUB finished in {seconds}s: {found['copies_checked']} copies checked on {found['nodes_checked']} nodes, "
        f"{found['corrupted']} corrupted, {found['missing']} missing (sent to repair), {found['orphans_removed']} unused copies removed")
    return {**scrub_state, "chunks_sent_to_repair": len(lost_on)}


@app.post("/scrub")
def scrub_now(orphan_grace: float = ORPHAN_GRACE):
    """Run a scrub immediately (it also runs on its own every SCRUB_INTERVAL seconds)."""
    with maintenance_lock:
        return run_scrub(orphan_grace)


def repair_loop():
    last_scrub = time.time()
    while True:
        time.sleep(REPAIR_INTERVAL)
        # Right after a restart we do not know the nodes yet: wait for their heartbeats.
        if time.time() - started_at < REPAIR_AFTER or not live_nodes():
            continue
        try:
            with maintenance_lock:
                if time.time() - last_scrub >= SCRUB_INTERVAL:
                    last_scrub = time.time()
                    run_scrub()
                run_repair()
        except Exception as error:   # keep the worker alive whatever happens
            repair["running"] = False
            scrub_state["running"] = False
            log(f"REPAIR/SCRUB error: {type(error).__name__}: {error}")
