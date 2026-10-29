"""Phase 8: generate the two test workloads (always the same files, thanks to fixed seeds).

  datasets/small: 5,000 text and JSON files of 1 to 50 KB, like documents and records.
                  250 of them are exact copies of other files, as happens with real data.
  datasets/large: 10 files of 100 MB to 1 GB:
                  8 "videos" of random data (incompressible, like real video),
                  2 disk images: disk-image-v2 is disk-image-v1 with 64 KB inserted
                  at 100 MB, like a new file added at the start of a disk.

A manifest with the SHA-256 of every file is written next to them, so the
benchmark can check that every downloaded file is identical.

Run:  python scripts/make_datasets.py            (both workloads, about 5 GB)
      python scripts/make_datasets.py --small    (only the small files)
"""
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "datasets"
MB = 1024 * 1024
WORDS = ("storage node chunk replica hash ring metadata gateway bucket object repair scrub cluster data file "
         "student course report network latency throughput version tag search index quorum parity piece "
         "the a of and to in is for on with by from as at be this that it are was will can").split()


def small(count=5000, duplicates=250):
    rng = random.Random(1)
    folder = ROOT / "small"
    folder.mkdir(parents=True, exist_ok=True)
    manifest, total = {}, 0
    originals = count - duplicates
    for i in range(originals):
        size = rng.randint(1024, 50 * 1024)
        if i % 2:
            records, length = [], 0
            while length < size - 200:        # stop just under the target, so the JSON stays valid
                record = {"id": len(records) + 1, "name": " ".join(rng.choices(WORDS, k=3)),
                          "value": round(rng.random() * 1000, 3), "tags": rng.sample(WORDS, 3)}
                records.append(record)
                length += len(json.dumps(record, indent=1)) + 4   # running total, no need to re-encode
            data = json.dumps({"file": i, "records": records}, indent=1).encode()
            if len(data) > size:   # the estimate was a bit low: keep the right share of the records
                records = records[: int(len(records) * size / len(data) * 0.97)]
                data = json.dumps({"file": i, "records": records}, indent=1).encode()
            name = f"record-{i:04d}.json"
        else:
            words = rng.choices(WORDS, k=size // 4)      # about 6 characters per word, more than enough
            name, data = f"document-{i:04d}.txt", " ".join(words).encode()[:size]
        (folder / name).write_bytes(data)
        manifest[name] = hashlib.sha256(data).hexdigest()
        total += len(data)
    names = sorted(manifest)
    for i in range(duplicates):        # exact copies of existing files under new names
        source = rng.choice(names)
        name = f"copy-{i:04d}-{source}"
        data = (folder / source).read_bytes()
        (folder / name).write_bytes(data)
        manifest[name] = manifest[source]
        total += len(data)
    (ROOT / "small-manifest.json").write_text(json.dumps(manifest, indent=0))
    print(f"small: {len(manifest)} files ({duplicates} duplicates), {total / MB:.1f} MB in {folder}")


def write_random(path, size_mb, seed):
    rng = random.Random(seed)
    digest = hashlib.sha256()
    with open(path, "wb") as f:
        for _ in range(size_mb // 16):
            block = rng.randbytes(16 * MB)
            f.write(block)
            digest.update(block)
    return digest.hexdigest()


def large():
    folder = ROOT / "large"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {}
    sizes = [112, 208, 304, 400, 496, 608, 800, 1008]       # MB, multiples of 16
    for i, size in enumerate(sizes, 1):
        name = f"video-{i:02d}.bin"
        started = time.time()
        manifest[name] = write_random(folder / name, size, seed=100 + i)
        print(f"large: {name} {size} MB ({time.time() - started:.0f} s)")
    manifest["disk-image-v1.img"] = write_random(folder / "disk-image-v1.img", 512, seed=200)
    # v2 = v1 with 64 KB inserted at 100 MB
    digest = hashlib.sha256()
    with open(folder / "disk-image-v1.img", "rb") as src, open(folder / "disk-image-v2.img", "wb") as dst:
        for i in range(32):
            block = src.read(16 * MB)
            if i == 100 // 16:
                cut = (100 % 16) * MB
                block = block[:cut] + random.Random(201).randbytes(64 * 1024) + block[cut:]
            dst.write(block)
            digest.update(block)
    manifest["disk-image-v2.img"] = digest.hexdigest()
    print("large: disk-image-v1.img 512 MB, disk-image-v2.img = v1 + 64 KB inserted at 100 MB")
    (ROOT / "large-manifest.json").write_text(json.dumps(manifest, indent=1))
    total = sum((folder / n).stat().st_size for n in manifest)
    print(f"large: {len(manifest)} files, {total / MB:,.0f} MB in {folder}")


if __name__ == "__main__":
    if "--large" not in sys.argv:
        small()
    if "--small" not in sys.argv:
        large()
