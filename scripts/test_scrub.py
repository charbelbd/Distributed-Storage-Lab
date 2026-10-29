"""Phase 7 test: periodic scrubbing.

We damage the stored data on purpose, directly on the nodes' disks:
  - flip a byte of one copy of a chunk      (silent corruption)
  - delete one copy of another chunk         (lost file)
  - put a chunk on a node that nobody uses   (leftover copy)
then run the scrubber. It must find all three; the repair worker must then
bring every chunk back to 3 good copies, and the leftover copy must be gone.

Run with the cluster up:  python scripts/test_scrub.py
"""
import hashlib
import os
import subprocess
import time
import uuid

import requests

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
KEY = {"X-API-Key": "bench-key-456"}
BUCKET = f"scrub-{uuid.uuid4().hex[:6]}"
DATA = os.urandom(9 * 1024 * 1024)          # 3 chunks


def api(method, path, **kw):
    return requests.request(method, GATEWAY + path, headers=KEY, timeout=600, **kw)


def on_node(node, command):
    subprocess.run(["docker", "compose", "exec", "-T", node, "sh", "-c", command], check=True, capture_output=True)


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


api("POST", "/buckets", json={"name": BUCKET, "redundancy": "replication"})
api("PUT", f"/buckets/{BUCKET}/objects/file.bin", data=DATA)
chunks = api("GET", f"/meta/{BUCKET}/file.bin").json()["chunks"]
print(f"Uploaded file.bin: {len(chunks)} chunks, 3 copies each\n")

c0, c1 = chunks[0], chunks[1]
bad_node, h0 = c0["nodes"][0], c0["hash"]
on_node(bad_node, f"printf 'X' | dd of=/data/{h0[:2]}/{h0} bs=1 seek=1000 conv=notrunc 2>/dev/null")
print(f"  damaged:  1 byte changed in chunk {h0[:12]} on {bad_node}")
gone_node, h1 = c1["nodes"][0], c1["hash"]
on_node(gone_node, f"rm /data/{h1[:2]}/{h1}")
print(f"  deleted:  chunk {h1[:12]} removed from {gone_node}")
orphan = os.urandom(5000)
orphan_hash = hashlib.sha256(orphan).hexdigest()
orphan_node = "node1"
requests.put(f"http://localhost:9001/chunks/{orphan_hash}", data=orphan)
print(f"  leftover: chunk {orphan_hash[:12]} stored on {orphan_node}, not used by any object\n")

print("Running the scrubber (every node re-reads all its chunks)...")
# orphan_grace=0: delete unused copies right away (normally only after 10 minutes)
r = api("POST", "/scrub?orphan_grace=0").json()
print(f"  {r['copies_checked']} copies checked ({r['bytes_checked'] / 2**20:.0f} MB) on {r['nodes_checked']} nodes in {r['duration_s']} s\n")
check("the corrupted copy is found", r["corrupted"] >= 1, f"{r['corrupted']} corrupted")
check("the deleted copy is found", r["missing"] >= 1, f"{r['missing']} missing")
check("the unused copy is removed", r["orphans_removed"] >= 1 and requests.head(f"http://localhost:9001/chunks/{orphan_hash}").status_code == 404,
      f"{r['orphans_removed']} unused copies removed")

started = time.time()
while time.time() - started < 60:
    chunks = api("GET", f"/meta/{BUCKET}/file.bin").json()["chunks"]
    if all(c["healthy_copies"] == 3 and len(c["nodes"]) == 3 for c in chunks):
        break
    time.sleep(1)
check("the repair worker brings every chunk back to 3 copies",
      all(len(c["nodes"]) == 3 for c in chunks), f"{time.time() - started:.0f} s; " + ", ".join(f"{c['hash'][:8]} on {'/'.join(c['nodes'])}" for c in chunks))
check("the file reads back correctly", api("GET", f"/buckets/{BUCKET}/objects/file.bin").content == DATA)

r = api("POST", "/scrub?orphan_grace=0").json()
check("a second scrub finds nothing wrong", r["corrupted"] == 0 and r["missing"] == 0,
      f"{r['corrupted']} corrupted, {r['missing']} missing")
