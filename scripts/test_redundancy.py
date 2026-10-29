import os
import subprocess
import time
import uuid

import requests

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
KEY = {"X-API-Key": "bench-key-456"}
RUN = uuid.uuid4().hex[:6]
REP, EC = f"rep-{RUN}", f"ec-{RUN}"
ALL = [f"node{i}" for i in range(1, 9)]
DATA = os.urandom(6 * 1024 * 1024)


def api(method, path, **kw):
    return requests.request(method, GATEWAY + path, headers={**KEY, **kw.pop("headers", {})}, timeout=120, **kw)


def compose(action, nodes):
    subprocess.run(["docker", "compose", action, *nodes], check=True, capture_output=True)
    print(f"     >> docker compose {action} {' '.join(nodes)}")


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


def wait_alive(count=8, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if sum(n["alive"] for n in api("GET", "/status").json()["nodes"]) >= count:
            return True
        time.sleep(1)
    return False


def read(bucket, key):
    try:
        r = api("GET", f"/buckets/{bucket}/objects/{key}")
        return r.status_code, (r.content == DATA if r.status_code == 200 else r.json().get("detail"))
    except requests.RequestException as error:
        return 0, f"download interrupted: {type(error).__name__}"


api("POST", "/buckets", json={"name": REP, "redundancy": "replication"})
api("POST", "/buckets", json={"name": EC, "redundancy": "ec"})
wait_alive()

print("1. Replication N=3, W=2, R=2")
r = api("PUT", f"/buckets/{REP}/objects/file.bin", data=DATA).json()
holders = api("GET", f"/meta/{REP}/file.bin").json()["chunks"][0]["nodes"]
print(f"     uploaded {r['size']:,} bytes in {r['chunks']} chunks; first chunk is on {', '.join(holders)}")
compose("kill", holders[:1])
code, result = read(REP, "file.bin")
check("one copy's node down: the read still works (2 copies confirm, R=2)", code == 200 and result is True)
compose("kill", holders[1:2])
code, result = read(REP, "file.bin")
check("two copies' nodes down: the read is refused (only 1 copy, R=2)", code == 503, result)
compose("start", holders[:2])
wait_alive()
code, result = read(REP, "file.bin")
check("nodes back: the read works again", code == 200 and result is True)

print("\n2. Write quorum W=2")
down = ALL[1:]
compose("kill", down)
r = api("PUT", f"/buckets/{REP}/objects/refused.bin", data=os.urandom(1000))
check("7 of 8 nodes down: the upload is refused", r.status_code == 503, r.json().get("detail"))
compose("start", down)
wait_alive()

print("\n3. Erasure coding 4+2")
r = api("PUT", f"/buckets/{EC}/objects/file.bin", data=DATA).json()
pieces = api("GET", f"/meta/{EC}/file.bin").json()["chunks"][0]["nodes"]
print(f"     uploaded in {r['chunks']} chunks; pieces of the first chunk on {', '.join(pieces)}")
compose("kill", pieces[:2])
code, result = read(EC, "file.bin")
check("2 nodes down: the chunk is rebuilt from the 4 remaining pieces", code == 200 and result is True)
compose("kill", pieces[2:3])
code, result = read(EC, "file.bin")
check("3 nodes down: only 3 pieces left, the read is refused", code == 503, result)
compose("start", pieces[:3])
wait_alive()

rep_meta, ec_meta = api("GET", f"/meta/{REP}/file.bin").json(), api("GET", f"/meta/{EC}/file.bin").json()
rep_disk = sum(c["size"] * len(c["nodes"]) for c in rep_meta["chunks"])
ec_disk = sum(-(-c["size"] // 4) * len(c["nodes"]) for c in ec_meta["chunks"])
print(f"     disk used for the same {len(DATA):,} bytes: replication {rep_disk / len(DATA):.1f}x, erasure coding {ec_disk / len(DATA):.1f}x")

print("\n4. Automatic repair")
victim = holders[2]
repaired_before = api("GET", "/status").json()["repair"]["total_repaired"]
compose("kill", [victim])
started = time.time()
degraded_seen = 0
while time.time() - started < 90:
    s = api("GET", "/status").json()
    degraded_seen = max(degraded_seen, s["degraded_objects"])
    if degraded_seen and s["degraded_objects"] == 0 and s["repair"]["total_repaired"] > repaired_before:
        break
    time.sleep(2)
seconds = time.time() - started
check(f"{victim} down: objects become degraded", degraded_seen > 0, f"{degraded_seen} degraded objects")
check("the repair worker rebuilds every lost copy and piece", s["degraded_objects"] == 0,
      f"{s['repair']['total_repaired'] - repaired_before} chunks repaired, healthy again {seconds:.0f} s after the crash")
after = api("GET", f"/meta/{REP}/file.bin").json()["chunks"][0]["nodes"]
check("the first chunk has 3 copies again, on healthy nodes", victim not in after and len(after) == 3, ", ".join(after))
pieces_after = api("GET", f"/meta/{EC}/file.bin").json()["chunks"][0]["nodes"]
check("the erasure-coded chunk has its 6 pieces again", victim not in pieces_after and len(set(pieces_after)) == 6, ", ".join(pieces_after))
code, result = read(EC, "file.bin")
check("both objects read back correctly", code == 200 and result is True and read(REP, "file.bin") == (200, True))
compose("start", [victim])
wait_alive()
