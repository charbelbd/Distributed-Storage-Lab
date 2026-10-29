"""Consistent hashing ring with virtual nodes.

Every storage node is placed on a circle many times (its virtual nodes). A chunk
goes to the first nodes found walking clockwise from the chunk's own position.
When a node joins or leaves, only the chunks next to its points on the circle
change owner, about 1/N of the data, instead of almost everything.
"""
import bisect
import hashlib

VIRTUAL_NODES = 100


def position(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


class HashRing:
    def __init__(self, nodes=(), virtual_nodes=VIRTUAL_NODES):
        self.virtual_nodes = virtual_nodes
        self.points = []   # sorted positions on the circle
        self.owner = {}    # position -> node id
        for node in nodes:
            self.add(node)

    def add(self, node):
        for i in range(self.virtual_nodes):
            p = position(f"{node}#{i}")
            self.owner[p] = node
            bisect.insort(self.points, p)

    def remove(self, node):
        self.points = [p for p in self.points if self.owner[p] != node]
        self.owner = {p: n for p, n in self.owner.items() if n != node}

    @property
    def nodes(self):
        return sorted(set(self.owner.values()))

    def lookup(self, chunk_hash: str, count: int = 1):
        """The `count` different nodes responsible for this chunk, in order."""
        if not self.points:
            return []
        count = min(count, len(self.nodes))
        start = bisect.bisect(self.points, int(chunk_hash[:16], 16))
        found = []
        for i in range(len(self.points)):
            node = self.owner[self.points[(start + i) % len(self.points)]]
            if node not in found:
                found.append(node)
                if len(found) == count:
                    break
        return found
