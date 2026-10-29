"""Storage node: keeps chunks on its local disk, named by their SHA-256 hash.

Endpoints
  PUT    /chunks/{hash}   store a chunk (rejected if the content does not match the hash)
  GET    /chunks/{hash}   read a chunk (the hash is checked again; corrupted chunks are refused)
  HEAD   /chunks/{hash}   does this node have the chunk? (size in Content-Length)
  DELETE /chunks/{hash}   remove a chunk
  POST   /scrub           re-check every chunk on this disk
  GET    /health          node id, free space, chunk count

Every few seconds the node sends a heartbeat to the metadata service.
"""
import hashlib
import os
import shutil
import threading
import time
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request, Response

NODE_ID = os.environ.get("NODE_ID", "node1")
PORT = int(os.environ.get("PORT", "9001"))
DATA_DIR = Path(os.environ.get("DATA_DIR", f"data/{NODE_ID}"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", f"http://localhost:{PORT}")
METADATA_URL = os.environ.get("METADATA_URL", "http://localhost:8000")
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "3"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "quarantine").mkdir(exist_ok=True)
app = FastAPI(title=f"storage node {NODE_ID}")


def log(message):
    print(f"{time.strftime('%H:%M:%S')} [{NODE_ID}] {message}", flush=True)


def chunk_path(chunk_hash: str) -> Path:
    """Chunks go in sub-folders named after the first 2 characters, so no folder gets too big."""
    if len(chunk_hash) != 64 or any(c not in "0123456789abcdef" for c in chunk_hash):
        raise HTTPException(400, "chunk id must be a 64-character lowercase SHA-256 hash")
    return DATA_DIR / chunk_hash[:2] / chunk_hash


def all_chunks():
    return [p for p in DATA_DIR.glob("??/*") if p.is_file() and not p.name.endswith(".tmp")]


@app.put("/chunks/{chunk_hash}", status_code=201)
async def put_chunk(chunk_hash: str, request: Request):
    path = chunk_path(chunk_hash)
    data = await request.body()
    actual = hashlib.sha256(data).hexdigest()
    if actual != chunk_hash:
        log(f"REJECT PUT {chunk_hash[:12]}: content hash is {actual[:12]}")
        raise HTTPException(400, "content does not match its SHA-256 hash")
    if path.exists():
        return {"stored": False, "reason": "already here"}
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)  # write then rename, so a crash never leaves half a chunk
    log(f"PUT {chunk_hash[:12]} ({len(data)} bytes)")
    return {"stored": True, "size": len(data)}


@app.get("/chunks/{chunk_hash}")
def get_chunk(chunk_hash: str):
    path = chunk_path(chunk_hash)
    if not path.exists():
        raise HTTPException(404, "chunk not found")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != chunk_hash:
        # The bytes on disk changed (bit rot, disk error, tampering): never serve them.
        path.replace(DATA_DIR / "quarantine" / chunk_hash)
        log(f"CORRUPTED {chunk_hash[:12]}: moved to quarantine")
        raise HTTPException(409, "chunk is corrupted on this node")
    return Response(data, media_type="application/octet-stream")


@app.head("/chunks/{chunk_hash}")
def head_chunk(chunk_hash: str):
    path = chunk_path(chunk_hash)
    if not path.exists():
        return Response(status_code=404)
    return Response(headers={"Content-Length": str(path.stat().st_size)})


@app.delete("/chunks/{chunk_hash}")
def delete_chunk(chunk_hash: str):
    path = chunk_path(chunk_hash)
    if not path.exists():
        raise HTTPException(404, "chunk not found")
    path.unlink()
    log(f"DELETE {chunk_hash[:12]}")
    return {"deleted": True}



@app.post("/scrub")
def scrub():
    """Read every chunk on this disk and check it against its hash.

    Corrupted chunks are moved to quarantine (like on a normal read). The answer
    lists the good chunks with their age, so the metadata service can find copies
    it does not know about, and the corrupted ones, so it can rebuild them elsewhere.
    """
    started = time.perf_counter()
    good, corrupted, checked_bytes = {}, [], 0
    for path in all_chunks():
        try:
            data = path.read_bytes()
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            continue  # deleted while we were scrubbing
        checked_bytes += len(data)
        if hashlib.sha256(data).hexdigest() == path.name:
            good[path.name] = round(age)
        else:
            path.replace(DATA_DIR / "quarantine" / path.name)
            corrupted.append(path.name)
            log(f"SCRUB found corrupted chunk {path.name[:12]}: moved to quarantine")
    seconds = time.perf_counter() - started
    log(f"SCRUB checked {len(good) + len(corrupted)} chunks ({checked_bytes} bytes) in {seconds:.2f}s, "
        f"{len(corrupted)} corrupted")
    return {"node_id": NODE_ID, "good": good, "corrupted": corrupted, "checked_bytes": checked_bytes,
            "seconds": round(seconds, 2)}


def status():
    chunks = all_chunks()
    return {
        "node_id": NODE_ID,
        "url": PUBLIC_URL,
        "free_bytes": shutil.disk_usage(DATA_DIR).free,
        "chunk_count": len(chunks),
        "used_bytes": sum(p.stat().st_size for p in chunks),
    }


@app.get("/health")
def health():
    return status()


def heartbeat_loop():
    connected = None
    while True:
        try:
            requests.post(f"{METADATA_URL}/heartbeat", json=status(), timeout=2).raise_for_status()
            if connected is not True:
                log(f"heartbeat OK -> {METADATA_URL}")
            connected = True
        except requests.RequestException as error:
            if connected is not False:
                log(f"heartbeat failed ({type(error).__name__}), will keep trying")
            connected = False
        time.sleep(HEARTBEAT_SECONDS)


@app.on_event("startup")
def start_heartbeat():
    log(f"starting on port {PORT}, data in {DATA_DIR}")
    threading.Thread(target=heartbeat_loop, daemon=True).start()
