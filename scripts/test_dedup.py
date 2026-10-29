"""Phase 3 test: deduplication with reference counting.

1. Upload a file, then upload the same content under a second name:
   the second upload sends no new chunks, it only adds references.
2. Delete the first object: the chunks stay, because the second one still uses them.
3. Delete the second object: now nobody uses the chunks, so they are removed from the nodes.

Run with the cluster up:  python scripts/test_dedup.py
"""
import os
import requests

API = os.environ.get("API_URL", "http://localhost:8000")


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


def node_chunk_counts():
    return {n["node_id"]: n["chunk_count"] for n in requests.get(f"{API}/nodes").json()}


def stats():
    s = requests.get(f"{API}/stats").json()
    return s["unique_chunks"], s["chunk_references"]


content = os.urandom(10 * 1024 * 1024 + 12345)  # about 10 MB -> 3 chunks of 4 MB
requests.delete(f"{API}/objects/dedup/original.bin")
requests.delete(f"{API}/objects/dedup/copy.bin")
before = stats()
print(f"File of {len(content):,} bytes, fixed 4 MB chunks, 3 copies of each chunk\n")

first = requests.put(f"{API}/objects/dedup/original.bin", data=content).json()
print(f"  upload original.bin: {first['chunks']} chunks, {first['new_chunks']} new, {first['deduplicated_chunks']} deduplicated")
second = requests.put(f"{API}/objects/dedup/copy.bin", data=content).json()
print(f"  upload copy.bin:     {second['chunks']} chunks, {second['new_chunks']} new, {second['deduplicated_chunks']} deduplicated")
check("the identical copy stores no new chunk", second["new_chunks"] == 0 and second["deduplicated_chunks"] == second["chunks"])
unique, refs = stats()
check("each chunk is stored once but referenced twice", unique - before[0] == first["chunks"] and refs - before[1] == 2 * first["chunks"],
      f"+{unique - before[0]} unique chunks, +{refs - before[1]} references")

back = requests.get(f"{API}/objects/dedup/copy.bin").content
check("reading copy.bin returns the exact content", back == content)

requests.delete(f"{API}/objects/dedup/original.bin")
unique, refs = stats()
check("after deleting original.bin the chunks are still there", unique - before[0] == first["chunks"],
      f"references now +{refs - before[1]}")
check("copy.bin can still be read", requests.get(f"{API}/objects/dedup/copy.bin").content == content)

r = requests.delete(f"{API}/objects/dedup/copy.bin").json()
unique, refs = stats()
check("after deleting copy.bin the unused chunks are removed", r["chunks_removed"] == first["chunks"] and unique == before[0],
      f"{r['chunks_removed']} chunks removed from the nodes")
print("\n  chunks per node now:", node_chunk_counts())
