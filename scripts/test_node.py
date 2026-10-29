import hashlib
import os
import subprocess
import requests

NODE = os.environ.get("NODE_URL", "http://localhost:9001")
CONTAINER_SERVICE = "node1"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))
    return ok


data = os.urandom(200_000)
h = hashlib.sha256(data).hexdigest()
print(f"Storage node {NODE}\nTest chunk: {len(data)} bytes, sha256 = {h[:16]}...\n")

r = requests.put(f"{NODE}/chunks/{h}", data=data)
check("PUT stores the chunk", r.status_code == 201, r.json())

r = requests.head(f"{NODE}/chunks/{h}")
check("HEAD finds it and gives its size", r.status_code == 200 and r.headers["Content-Length"] == str(len(data)),
      f"Content-Length {r.headers.get('Content-Length')}")

r = requests.get(f"{NODE}/chunks/{h}")
check("GET returns the same bytes (hash checked)", r.status_code == 200 and r.content == data)

wrong = "0" * 64
r = requests.put(f"{NODE}/chunks/{wrong}", data=data)
check("PUT with a hash that does not match the content is rejected", r.status_code == 400, r.json()["detail"])

r = requests.delete(f"{NODE}/chunks/{h}")
check("DELETE removes it", r.status_code == 200 and requests.head(f"{NODE}/chunks/{h}").status_code == 404)

print("\nCorruption on disk")
data = os.urandom(50_000)
h = hashlib.sha256(data).hexdigest()
requests.put(f"{NODE}/chunks/{h}", data=data)
subprocess.run(["docker", "compose", "exec", "-T", CONTAINER_SERVICE, "sh", "-c",
                f"printf 'XXXX' | dd of=/data/{h[:2]}/{h} bs=1 seek=100 conv=notrunc 2>/dev/null"], check=True)
print(f"  changed 4 bytes of chunk {h[:16]}... inside the {CONTAINER_SERVICE} container")
r = requests.get(f"{NODE}/chunks/{h}")
check("GET refuses the corrupted chunk", r.status_code == 409, r.json()["detail"])
check("the corrupted chunk is moved to quarantine", requests.head(f"{NODE}/chunks/{h}").status_code == 404)

print("\nNode status (also sent to the metadata service every 3 s as a heartbeat)")
print(" ", requests.get(f"{NODE}/health").json())
