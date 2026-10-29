import numpy as np

FIXED_SIZE = 4 * 1024 * 1024

CDC_MIN = 1 * 1024 * 1024
CDC_AVG = 4 * 1024 * 1024
CDC_MAX = 16 * 1024 * 1024
BITS_SMALL = 24
BITS_LARGE = 20

GEAR = np.random.default_rng(2026).integers(0, 2**64, size=256, dtype=np.uint64, endpoint=False)
WINDOW = 64


def fixed_chunks(blocks, size=FIXED_SIZE):
    buffer = bytearray()
    for block in blocks:
        buffer += block
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


def gear_hashes(data: bytes) -> np.ndarray:
    hashes = GEAR[np.frombuffer(data, dtype=np.uint8)]
    width = 1
    while width < WINDOW:
        older = np.zeros_like(hashes)
        older[width:] = hashes[:-width] << np.uint64(width)
        hashes = hashes + older
        width *= 2
    return hashes


def find_cut(hashes: np.ndarray, length: int) -> int:
    if length <= CDC_MIN:
        return length
    end_small = min(CDC_AVG, length)
    candidates = np.nonzero((hashes[CDC_MIN:end_small] >> np.uint64(64 - BITS_SMALL)) == 0)[0]
    if len(candidates):
        return CDC_MIN + int(candidates[0]) + 1
    end_large = min(CDC_MAX, length)
    if end_large > end_small:
        candidates = np.nonzero((hashes[end_small:end_large] >> np.uint64(64 - BITS_LARGE)) == 0)[0]
        if len(candidates):
            return end_small + int(candidates[0]) + 1
    return end_large


def cdc_chunks(blocks):
    buffer = bytearray()
    hashes = np.empty(0, dtype=np.uint64)
    tail = b""
    finished = False
    blocks = iter(blocks)
    while True:
        while not finished and len(buffer) < CDC_MAX:
            block = next(blocks, None)
            if block is None:
                finished = True
                continue
            hashes = np.concatenate((hashes, gear_hashes(tail + block)[len(tail):]))
            tail = (tail + block)[-(WINDOW - 1):]
            buffer += block
        if not buffer:
            return
        if finished and len(buffer) <= CDC_MIN:
            yield bytes(buffer)
            return
        cut = find_cut(hashes, min(len(buffer), CDC_MAX))
        yield bytes(buffer[:cut])
        del buffer[:cut]
        hashes = hashes[cut:]


def chunk(blocks, method="fixed"):
    return cdc_chunks(blocks) if method == "cdc" else fixed_chunks(blocks)
