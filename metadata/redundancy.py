import hashlib
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zfec.easyfec import Decoder, Encoder

http = requests.Session()
http.mount("http://", HTTPAdapter(pool_maxsize=32, max_retries=Retry(
    total=2, connect=2, read=1, status=0, backoff_factor=0.05, allowed_methods=None, raise_on_status=False)))
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


def write_replicated(data, chunk_hash, targets, url_of, write_quorum):
    results = list(pool.map(lambda n: put(url_of(n), chunk_hash, data), targets))
    stored = [n for n, ok in zip(targets, results) if ok]
    if len(stored) < write_quorum:
        raise QuorumError(f"write quorum not met: {len(stored)} of {len(targets)} copies stored, W={write_quorum}")
    return stored


def read_replicated(chunk_hash, holders, url_of, read_quorum):
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


def encode(data, k, m):
    pieces = Encoder(k, k + m).encode(data)
    padding = len(pieces[0]) * k - len(data)
    return pieces, padding


def decode(pieces_by_index, k, m, padding):
    indexes = sorted(pieces_by_index)[:k]
    return Decoder(k, k + m).decode([pieces_by_index[i] for i in indexes], indexes, padding)


def write_ec(data, targets, url_of, k, m):
    pieces, padding = encode(data, k, m)
    hashes = [sha(p) for p in pieces]
    results = list(pool.map(lambda i: put(url_of(targets[i]), hashes[i], pieces[i]), range(k + m)))
    stored = sum(results)
    if stored < k + 1:
        raise QuorumError(f"only {stored} of {k + m} pieces stored, need at least {k + 1}")
    nodes = [t if ok else "" for t, ok in zip(targets, results)]
    return hashes, nodes, padding


def read_pieces(piece_hashes, nodes, url_of, k, skip=()):
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
