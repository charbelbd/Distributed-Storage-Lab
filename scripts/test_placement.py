"""Phase 3 test: how many chunks move when a node is added or removed?

For every chunk we look at which nodes the consistent hashing ring would choose
before and after the change, and count the chunks whose primary node changed, and the share of all copies
(3 per chunk) that have to be copied to another node.
We do the same with simple "hash mod number of nodes" placement for comparison.

Uses the real chunk hashes stored in the system, plus 100,000 random hashes so
the percentages are stable.  Run:  python scripts/test_placement.py
"""
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from metadata.ring import HashRing  # noqa: E402

API = os.environ.get("API_URL", "http://localhost:8000")
try:
    real = list(requests.get(f"{API}/chunk-hashes", timeout=5).json())
except requests.RequestException:
    real = []
synthetic = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(100_000)]
hashes = real + synthetic
nodes = [f"node{i}" for i in range(1, 9)]   # the 8 storage nodes of the cluster


def ring_moves(before, after):
    a, b = HashRing(before), HashRing(after)
    primary = sum(1 for h in hashes if a.lookup(h)[0] != b.lookup(h)[0])
    # Of all the stored copies (3 per chunk), how many must be copied to a different node?
    moved_copies = sum(len(set(b.lookup(h, 3)) - set(a.lookup(h, 3))) for h in hashes)
    return 100 * primary / len(hashes), 100 * moved_copies / (3 * len(hashes))


def modulo_moves(before, after):
    pick = lambda h, ns: ns[int(h[:16], 16) % len(ns)]
    return 100 * sum(1 for h in hashes if pick(h, before) != pick(h, after)) / len(hashes)


def share(ring_nodes):
    ring = HashRing(ring_nodes)
    counts = {n: 0 for n in ring_nodes}
    for h in hashes:
        counts[ring.lookup(h)[0]] += 1
    return ", ".join(f"{n} {100 * c / len(hashes):.1f}%" for n, c in counts.items())


print(f"{len(hashes):,} chunk hashes ({len(real)} real from the system + {len(synthetic):,} random)")
print(f"Ring: {HashRing().virtual_nodes} virtual nodes per storage node\n")
print("Load with 8 nodes:", share(nodes), "\n")

print(f"{'change':<28}{'ideal':>8}{'ring (primary)':>17}{'ring (all 3 copies)':>22}{'hash mod N':>13}")
for label, before, after, ideal in [
    ("add node9 (8 -> 9 nodes)", nodes, nodes + ["node9"], 100 / 9),
    ("remove node8 (8 -> 7 nodes)", nodes, nodes[:7], 100 / 8),
]:
    primary, replicas = ring_moves(before, after)
    print(f"{label:<28}{ideal:>7.1f}%{primary:>16.1f}%{replicas:>21.1f}%{modulo_moves(before, after):>12.1f}%")

print("\nThe ring moves about 1/N of the chunks, close to the ideal. Modulo placement moves most of them.")
