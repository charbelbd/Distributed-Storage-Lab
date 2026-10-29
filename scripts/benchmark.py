import hashlib
import json
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

GATEWAY = "http://localhost:8080"
KEY = {"X-API-Key": "bench-key-456"}
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "datasets"
RESULTS = ROOT / "results"
RUN = uuid.uuid4().hex[:4]
MB = 1024 * 1024
session = requests.Session()


def api(method, path, **kw):
    return session.request(method, GATEWAY + path, headers={**KEY, **kw.pop("headers", {})}, timeout=3600, **kw)


def status():
    return api("GET", "/status").json()


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def upload(bucket, path, chunking="fixed"):
    started = time.perf_counter()
    with open(path, "rb") as f:
        r = api("PUT", f"/buckets/{bucket}/objects/{path.name}?chunking={chunking}", data=f)
    r.raise_for_status()
    return time.perf_counter() - started, r.json()


def download(bucket, name, expected):
    started = time.perf_counter()
    digest, size = hashlib.sha256(), 0
    with session.get(f"{GATEWAY}/buckets/{bucket}/objects/{name}", headers=KEY, stream=True, timeout=3600) as r:
        r.raise_for_status()
        for block in r.iter_content(MB):
            digest.update(block)
            size += len(block)
    return time.perf_counter() - started, size, digest.hexdigest() == expected


def run_pass(label, bucket, redundancy, files, manifest, workers, chunking="fixed"):
    api("POST", "/buckets", json={"name": bucket, "redundancy": redundancy})
    before = status()
    total = sum(p.stat().st_size for p in files)
    print(f"\n{label}: {len(files)} files, {total / MB:,.1f} MB, bucket {bucket} ({redundancy}, {chunking} chunks)")

    started = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        ups = list(pool.map(lambda p: upload(bucket, p, chunking), files))
    up_seconds = time.perf_counter() - started
    after = status()
    latencies = [u[0] * 1000 for u in ups]
    new_chunks = sum(u[1]["new_chunks"] for u in ups)
    dedup_chunks = sum(u[1]["deduplicated_chunks"] for u in ups)

    started = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        downs = list(pool.map(lambda p: download(bucket, p.name, manifest[p.name]), files))
    down_seconds = time.perf_counter() - started
    verified = sum(1 for d in downs if d[2])

    result = {
        "label": label, "bucket": bucket, "redundancy": redundancy, "chunking": chunking,
        "files": len(files), "bytes": total, "clients": workers,
        "upload_seconds": round(up_seconds, 2), "upload_files_per_s": round(len(files) / up_seconds, 1),
        "upload_mb_per_s": round(total / MB / up_seconds, 1),
        "upload_latency_ms": {"p50": round(pct(latencies, 50), 1), "p95": round(pct(latencies, 95), 1), "p99": round(pct(latencies, 99), 1)},
        "download_seconds": round(down_seconds, 2), "download_files_per_s": round(len(files) / down_seconds, 1),
        "download_mb_per_s": round(total / MB / down_seconds, 1),
        "verified": f"{verified}/{len(files)}",
        "chunks_new": new_chunks, "chunks_deduplicated": dedup_chunks,
        "unique_bytes_added": after["unique_bytes"] - before["unique_bytes"],
        "disk_bytes_added": after["stored_bytes"] - before["stored_bytes"],
    }
    result["disk_per_byte"] = round(result["disk_bytes_added"] / total, 2) if total else None
    print(f"  upload   {result['upload_files_per_s']:>8} files/s {result['upload_mb_per_s']:>8} MB/s   "
          f"latency p50 {result['upload_latency_ms']['p50']} ms, p95 {result['upload_latency_ms']['p95']} ms, p99 {result['upload_latency_ms']['p99']} ms")
    print(f"  download {result['download_files_per_s']:>8} files/s {result['download_mb_per_s']:>8} MB/s   "
          f"verified by SHA-256: {result['verified']}")
    print(f"  chunks   {new_chunks} new, {dedup_chunks} deduplicated   disk used: {result['disk_bytes_added'] / MB:,.1f} MB "
          f"= {result['disk_per_byte']} x the data")
    return result


def search_timing(bucket):
    timings = []
    for prefix in ["document-00", "record-01", "copy-", "document-4"]:
        started = time.perf_counter()
        r = api("GET", "/search", params={"bucket": bucket, "prefix": prefix, "limit": 1000}).json()
        timings.append(((time.perf_counter() - started) * 1000, prefix, r["count"], r["engine"]))
    for ms, prefix, count, engine in timings:
        print(f"  search prefix={prefix:<12} {count:>5} results in {ms:6.1f} ms ({engine})")
    return [{"prefix": p, "results": c, "ms": round(ms, 1), "engine": e} for ms, p, c, e in timings]


def save(name, results):
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"benchmark-{name}.json").write_text(json.dumps(results, indent=2))
    rows = ["| Pass | Files | Size | Upload | Upload latency p50/p95/p99 | Download | Verified | Disk / data |",
            "|---|---|---|---|---|---|---|---|"]
    for r in results["passes"]:
        lat = r["upload_latency_ms"]
        rows.append(f"| {r['label']} | {r['files']} | {r['bytes'] / MB:,.1f} MB | {r['upload_files_per_s']} files/s, "
                    f"{r['upload_mb_per_s']} MB/s | {lat['p50']} / {lat['p95']} / {lat['p99']} ms | "
                    f"{r['download_files_per_s']} files/s, {r['download_mb_per_s']} MB/s | {r['verified']} | {r['disk_per_byte']}x |")
    (RESULTS / f"benchmark-{name}.md").write_text(f"# Benchmark: {name} workload\n\nRun {results['date']}\n\n" + "\n".join(rows) + "\n")
    print(f"\nSaved results/benchmark-{name}.json and results/benchmark-{name}.md")


def main(workload):
    manifest = json.loads((DATA / f"{workload}-manifest.json").read_text())
    files = sorted((DATA / workload / name for name in manifest), key=lambda p: p.name)
    print(f"Workload {workload}: {len(files)} files, nodes up: {sum(n['alive'] for n in status()['nodes'])}")
    results = {"workload": workload, "date": time.strftime("%Y-%m-%d %H:%M"), "passes": []}
    if workload == "small":
        results["passes"].append(run_pass("small, replication x3", f"small-rep-{RUN}", "replication", files, manifest, 16))
        results["search"] = search_timing(f"small-rep-{RUN}")
        results["passes"].append(run_pass("small, erasure coding 4+2", f"small-ec-{RUN}", "ec", files, manifest, 16))
    else:
        results["passes"].append(run_pass("large, replication x3", f"large-rep-{RUN}", "replication", files, manifest, 2))
        results["passes"].append(run_pass("large, erasure coding 4+2", f"large-ec-{RUN}", "ec", files, manifest, 2))
        images = [p for p in files if p.name.startswith("disk-image")]
        for chunking in ("fixed", "cdc"):
            bucket = f"images-{chunking}-{RUN}"
            api("POST", "/buckets", json={"name": bucket, "redundancy": "replication"})
            first = upload(bucket, images[0], chunking)[1]
            second = upload(bucket, images[1], chunking)[1]
            reused = second["deduplicated_chunks"] / second["chunks"]
            print(f"\ndisk-image-v2 after v1, {chunking} chunks: {second['deduplicated_chunks']} of {second['chunks']} chunks "
                  f"already stored ({100 * reused:.0f}% deduplicated), {second['seconds']} s")
            results[f"disk_image_dedup_{chunking}"] = {"v1_chunks": first["chunks"], "v2_chunks": second["chunks"],
                                                       "v2_deduplicated": second["deduplicated_chunks"],
                                                       "percent": round(100 * reused, 1), "seconds": second["seconds"]}
    save(workload, results)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("small", "large"):
        sys.exit("usage: python scripts/benchmark.py small|large")
    main(sys.argv[1])
