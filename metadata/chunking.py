"""Two ways to cut a file into chunks.

fixed_chunks: every chunk is exactly CHUNK_SIZE bytes (the last one can be smaller).
    Simple and fast, but inserting one byte at the start of a file shifts every
    chunk boundary, so none of the old chunks can be reused.

cdc_chunks: content-defined chunking in the style of FastCDC. A rolling "gear"
    hash is computed over the last 64 bytes at every position, and a chunk ends
    where the hash matches a bit pattern. Because the boundaries depend on the
    content itself, an insertion only changes the chunks around it.

Both take an iterator of byte blocks (so a 1 GB upload never has to fit in memory)
and yield the chunks one by one.
"""
import numpy as np

FIXED_SIZE = 4 * 1024 * 1024

# FastCDC sizes: average around 4 MB to compare fairly with fixed 4 MB chunks.
CDC_MIN = 1 * 1024 * 1024
CDC_AVG = 4 * 1024 * 1024
CDC_MAX = 16 * 1024 * 1024
# Normalized chunking (FastCDC): a stricter mask before the average size and a
# looser one after it, so chunk sizes stay close to the average.
# A cut happens where the top bits of the hash are all zero (the top bits mix all
# 64 bytes of the window; the lowest bits only depend on the last few bytes).
BITS_SMALL = 24   # before the average size: 24 zero bits needed, harder to match
BITS_LARGE = 20   # after the average size: 20 zero bits, easier to match

# 256 random 64-bit numbers, one per byte value (fixed seed so every run cuts the same way).
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
    """Hash of the 64 bytes ending at each position: sum of GEAR[byte] << age."""
    # Built by doubling the window: a 2k-byte hash is the k-byte hash plus the
    # k-byte hash from k positions earlier, shifted by k. Six steps reach 64 bytes.
    # numpy uint64 arithmetic wraps around exactly like the real rolling hash.
    hashes = GEAR[np.frombuffer(data, dtype=np.uint8)]
    width = 1
    while width < WINDOW:
        older = np.zeros_like(hashes)
        older[width:] = hashes[:-width] << np.uint64(width)
        hashes = hashes + older
        width *= 2
    return hashes


def find_cut(hashes: np.ndarray, length: int) -> int:
    """Where should the chunk that starts at position 0 end?"""
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
    # The hash at a position only depends on the 64 bytes before it, so each byte
    # is hashed once when it arrives, and the hashes are kept next to the buffer.
    buffer = bytearray()
    hashes = np.empty(0, dtype=np.uint64)
    tail = b""               # last 63 bytes seen, the context for the next block
    finished = False
    blocks = iter(blocks)
    while True:
        # Keep at least one maximum-size chunk in the buffer so a cut can always be found.
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
