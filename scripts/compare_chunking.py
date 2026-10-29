"""Phase 3 test: fixed-size chunking compared with content-defined chunking (FastCDC).

We take a 200 MB file, then make a second version with 100 bytes inserted near
the start (like editing a document or a disk image). For each method we count how
many chunks of the new version already exist in the old one, i.e. can be deduplicated.

Run:  python scripts/compare_chunking.py
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from metadata.chunking import cdc_chunks, fixed_chunks  # noqa: E402

SIZE = 200 * 1024 * 1024
original = os.urandom(SIZE)
edited = original[:5000] + os.urandom(100) + original[5000:]


def blocks(data, size=1024 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def run(name, method):
    started = time.perf_counter()
    old = [hashlib.sha256(c).hexdigest() for c in method(blocks(original))]
    seconds = time.perf_counter() - started
    new_chunks = list(method(blocks(edited)))
    new = [hashlib.sha256(c).hexdigest() for c in new_chunks]
    shared = sum(1 for h in new if h in set(old))
    sizes = [len(c) for c in new_chunks]
    print(f"{name:<22}{len(old):>7}{sum(sizes) // len(sizes) // 1024:>11} KB{min(sizes) // 1024:>9} KB{max(sizes) // 1024:>9} KB"
          f"{shared:>8}/{len(new):<4}{100 * shared / len(new):>8.1f}%{SIZE / seconds / 2**20:>9.0f} MB/s")


print(f"File: {SIZE // 2**20} MB of random data. Edited copy: 100 bytes inserted at byte 5,000.\n")
print(f"{'method':<22}{'chunks':>7}{'avg size':>14}{'min':>12}{'max':>12}{'reused':>13}{'dedup':>9}{'speed':>14}")
run("fixed 4 MB", fixed_chunks)
run("FastCDC (avg ~4 MB)", cdc_chunks)
print("\nWith fixed chunks the insertion shifts every boundary after it, so almost nothing can be reused.")
print("With content-defined chunks the boundaries move with the content, so only the first chunk changes.")
