"""How a chunk is kept safe on the storage nodes.

Replication: the chunk is copied to N nodes. A write succeeds when W nodes have
    stored it, and a read needs R nodes to confirm they hold a good copy.
    With W + R > N, every read overlaps at least one node of every successful write.

Erasure coding (Reed-Solomon k + m, with zfec): the chunk is cut into k data
    pieces and m parity pieces are computed, each piece on a different node.
    Any k of the k + m pieces rebuild the chunk, so m nodes can be lost, and the
    extra storage is only (k + m) / k = 1.5x for 4 + 2, instead of 3x for 3 copies.

Every piece is itself content-addressed: it is stored under the SHA-256 of its bytes,
so the storage nodes check pieces exactly like they check whole chunks.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import requests
from zfec.easyfec import Decoder, Encoder

http = requests.Session()
pool = ThreadPoolExecutor(max_workers=16)


class QuorumError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put(url, piece_hash, data):
    try:
        http.put(f"{url}/chunks/{piece_hash}", data=data, timeout=30).raise_for_status()
        return True
    except requests.RequestException:
        return False


def get(url, piece_hash):
    """The piece's bytes if this node returns a correct copy, otherwise None."""
    try:
        r = http.get(f"{url}/chunks/{piece_hash}", timeout=30)
        return r.content if r.status_code == 200 and sha(r.content) == piece_hash else None
    except requests.RequestException:
        return None


def has(url, piece_hash):
    try:
        return http.head(f"{url}/chunks/{piece_hash}", timeout=10).status_code == 200
    except requests.RequestException:
        return False


# ---------- replication ----------

def write_replicated(data, chunk_hash, targets, url_of, write_quorum):
    """Send the chunk to every target node in parallel; succeed if at least W stored it."""
    results = list(pool.map(lambda n: put(url_of(n), chunk_hash, data), targets))
    stored = [n for n, ok in zip(targets, results) if ok]
    if len(stored) < write_quorum:
        raise QuorumError(f"write quorum not met: {len(stored)} of {len(targets)} copies stored, W={write_quorum}")
    return stored


def read_replicated(chunk_hash, holders, url_of, read_quorum):
    """Read one good copy and make sure at least R holders confirm they have it."""
    data, confirmed = None, 0
    for node in holders:
        url = url_of(node)
        if not url:
            continue
        if data is None:
            data = get(url, chunk_hash)
            confirmed += data is not None
        elif has(url, chunk_hash):
            confirmed += 1
        if data is not None and confirmed >= read_quorum:
            return data
    raise QuorumError(f"read quorum not met: {confirmed} of {len(holders)} copies confirmed, R={read_quorum}")


# ---------- erasure coding ----------

def encode(data, k, m):
    """Split into k data pieces + m parity pieces (all the same size)."""
    pieces = Encoder(k, k + m).encode(data)
    padding = len(pieces[0]) * k - len(data)
    return pieces, padding


def decode(pieces_by_index, k, m, padding):
    indexes = sorted(pieces_by_index)[:k]
    return Decoder(k, k + m).decode([pieces_by_index[i] for i in indexes], indexes, padding)


def write_ec(data, targets, url_of, k, m):
    """Store piece i on targets[i]. At least k + 1 pieces must be stored (the chunk survives one more loss)."""
    pieces, padding = encode(data, k, m)
    hashes = [sha(p) for p in pieces]
    results = list(pool.map(lambda i: put(url_of(targets[i]), hashes[i], pieces[i]), range(k + m)))
    stored = sum(results)
    if stored < k + 1:
        raise QuorumError(f"only {stored} of {k + m} pieces stored, need at least {k + 1}")
    nodes = [t if ok else "" for t, ok in zip(targets, results)]   # "" = piece missing, to be repaired
    return hashes, nodes, padding


def read_pieces(piece_hashes, nodes, url_of, k, skip=()):
    """Collect k good pieces, trying the nodes in order."""
    found = {}
    for i, (piece_hash, node) in enumerate(zip(piece_hashes, nodes)):
        if len(found) == k:
            break
        if i in skip or not node or not url_of(node):
            continue
        data = get(url_of(node), piece_hash)
        if data is not None:
            found[i] = data
    return found


def read_ec(chunk_hash, piece_hashes, nodes, url_of, k, m, padding):
    found = read_pieces(piece_hashes, nodes, url_of, k)
    if len(found) < k:
        raise QuorumError(f"only {len(found)} of {k + m} pieces available, need {k} to rebuild the chunk")
    data = decode(found, k, m, padding)
    if sha(data) != chunk_hash:
        raise QuorumError("rebuilt chunk does not match its hash")
    return data
