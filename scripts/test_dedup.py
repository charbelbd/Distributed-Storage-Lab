import os
import uuid

import requests

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
KEY = {"X-API-Key": "bench-key-456"}
BUCKET = f"dedup-{uuid.uuid4().hex[:6]}"
OBJ = f"{GATEWAY}/buckets/{BUCKET}/objects"


def api(method, url, **kw):
    return requests.request(method, url, headers=KEY, timeout=120, **kw)


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


def node_chunk_counts():
    return {n["node_id"]: n["chunks_assigned"] for n in api("GET", f"{GATEWAY}/status").json()["nodes"]}


def stats():
    s = api("GET", f"{GATEWAY}/status").json()
    refs = sum(len(o) for o in [api("GET", f"{GATEWAY}/meta/{BUCKET}/{k}").json()["chunk_hashes"]
                                for k in [o["key"] for o in api("GET", f"{OBJ}").json()]])
    return s["unique_chunks"], refs


content = os.urandom(10 * 1024 * 1024 + 12345)
api("POST", f"{GATEWAY}/buckets", json={"name": BUCKET, "redundancy": "replication"})
before = stats()
print(f"File of {len(content):,} bytes, fixed 4 MB chunks, 3 copies of each chunk\n")

first = api("PUT", f"{OBJ}/original.bin", data=content).json()
print(f"  upload original.bin: {first['chunks']} chunks, {first['new_chunks']} new, {first['deduplicated_chunks']} deduplicated")
second = api("PUT", f"{OBJ}/copy.bin", data=content).json()
print(f"  upload copy.bin:     {second['chunks']} chunks, {second['new_chunks']} new, {second['deduplicated_chunks']} deduplicated")
check("the identical copy stores no new chunk", second["new_chunks"] == 0 and second["deduplicated_chunks"] == second["chunks"])
unique, refs = stats()
check("each chunk is stored once but referenced twice", unique - before[0] == first["chunks"] and refs - before[1] == 2 * first["chunks"],
      f"+{unique - before[0]} unique chunks, +{refs - before[1]} references")

back = api("GET", f"{OBJ}/copy.bin").content
check("reading copy.bin returns the exact content", back == content)

api("DELETE", f"{OBJ}/original.bin")
unique, refs = stats()
check("after deleting original.bin the chunks are still there", unique - before[0] == first["chunks"],
      f"references now +{refs - before[1]}")
check("copy.bin can still be read", api("GET", f"{OBJ}/copy.bin").content == content)

r = api("DELETE", f"{OBJ}/copy.bin").json()
unique, refs = stats()
check("after deleting copy.bin the unused chunks are removed", r["chunks_removed"] == first["chunks"] and unique == before[0],
      f"{r['chunks_removed']} chunks removed from the nodes")
print("\n  chunk copies per node now:", node_chunk_counts())
