"""Draws the three flowcharts of the report (write, read, failure and repair path) as SVG
inside HTML pages; render_images.ps1 turns them into PNG files with headless Edge.

Each flowchart is a list of steps going down the page. A decision can send one
outcome to a box on the right ("side"), and a step can receive an arrow from a
box on the left ("feed"), used for the scrubber in the repair flowchart.
"""
import html
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flowcharts")
W, MAIN_X, MAIN_W, SIDE_X, SIDE_W, FEED_X = 1400, 420, 560, 1060, 300, 40
COLORS = {"start": ("#1f2937", "#ffffff"), "step": ("#e8f0fe", "#1d2330"), "decision": ("#fff4d6", "#1d2330"),
          "end": ("#14532d", "#ffffff"), "bad": ("#fde2e2", "#7f1d1d"), "good": ("#dcfce7", "#14532d"), "note": ("#f3f4f6", "#1d2330")}


def wrap(text, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + [line]


def text_block(lines, cx, cy, color, size=21, bold=False):
    start = cy - (len(lines) - 1) * size * 0.6
    return "".join(f'<text x="{cx}" y="{start + i * size * 1.2 + size * 0.35}" text-anchor="middle" font-size="{size}" '
                   f'fill="{color}" font-weight="{700 if bold else 400}">{html.escape(l)}</text>' for i, l in enumerate(lines))


def box(kind, x, y, w, text):
    fill, ink = COLORS[kind]
    lines = wrap(text, 40 if w > 400 else 22)
    h = max(70, 28 * len(lines) + 30)
    cx, cy = x + w / 2, y + h / 2
    if kind == "decision":
        h = max(120, 28 * len(lines) + 70)
        cy = y + h / 2
        shape = f'<polygon points="{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}" fill="{fill}" stroke="#b7791f" stroke-width="2.5"/>'
        lines = wrap(text, 30)
    else:
        r = 35 if kind in ("start", "end") else 10
        shape = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="#94a3b8" stroke-width="2"/>'
    return shape + text_block(lines, cx, cy, ink, bold=kind in ("start", "end")), h


def arrow(x1, y1, x2, y2, label=None):
    """Arrow from (x1, y1) to (x2, y2). The label sits next to the start of the arrow."""
    out = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#475569" stroke-width="2.5" marker-end="url(#a)"/>')
    if label:
        lx, ly = (x1 + 10, y1 + 22) if x1 == x2 else (x1 + 6, y1 - 10)   # down arrow: right of it / side arrow: above it
        out += f'<text x="{lx}" y="{ly}" font-size="19" fill="#475569" font-weight="700">{html.escape(label)}</text>'
    return out


def flowchart(title, steps):
    parts, y, gap = [], 90, 48
    parts.append(f'<text x="{W / 2}" y="50" text-anchor="middle" font-size="30" font-weight="700" fill="#111827">{html.escape(title)}</text>')
    prev_bottom, prev_label = None, None
    for s in steps:
        kind, text = s["kind"], s["text"]
        x, w = (MAIN_X - 40, MAIN_W + 80) if kind == "decision" else (MAIN_X, MAIN_W)
        shape, h = box(kind, x, y, w, text)
        if prev_bottom is not None:
            # The label of a decision belongs to the arrow that leaves it downwards.
            parts.append(arrow(MAIN_X + MAIN_W / 2, prev_bottom, MAIN_X + MAIN_W / 2, y, prev_label))
        prev_label = s.get("down_label")
        parts.append(shape)
        mid = y + h / 2
        if "side" in s:
            side_kind, side_text, side_label = s["side"]
            side_shape, sh = box(side_kind, SIDE_X, mid - 45, SIDE_W, side_text)
            parts.append(side_shape)
            parts.append(arrow(x + w, mid, SIDE_X, mid, side_label))
        if "feed" in s:
            feed_shape, fh = box("note", FEED_X, mid - 60, 300, s["feed"])
            parts.append(feed_shape)
            parts.append(arrow(FEED_X + 300, mid, x, mid))
        prev_bottom = y + h
        y += h + gap
    height = int(y + 20)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" '
           f'font-family="Segoe UI, Arial, sans-serif"><defs><marker id="a" markerWidth="12" markerHeight="12" refX="10" '
           f'refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#475569"/></marker></defs>'
           f'<rect width="{W}" height="{height}" fill="#ffffff"/>{"".join(parts)}</svg>')
    return svg, height


def S(kind, text, **extra):
    return {"kind": kind, "text": text, **extra}


CHARTS = {
    "write-path": ("Write path: uploading an object", [
        S("start", "Client sends PUT /buckets/{bucket}/objects/{key} with its API key"),
        S("decision", "Valid API key and under the rate limit?", side=("bad", "401 Unauthorized or 429 Too Many Requests", "no"), down_label="yes"),
        S("step", "Gateway streams the file to the metadata service, which saves it to a temporary file"),
        S("step", "File cut into chunks: fixed 4 MB, or content-defined (FastCDC)"),
        S("step", "For each chunk: its SHA-256 hash becomes its name"),
        S("decision", "Chunk already stored with the same protection?", side=("good", "Count one more reference (deduplication), nothing is sent", "yes"), down_label="no"),
        S("step", "Hash ring picks the nodes: 3 for replication, 6 for erasure coding 4+2 (4 data + 2 parity pieces)"),
        S("step", "Copies or pieces sent in parallel; each node checks the SHA-256 before saving"),
        S("decision", "Enough stored? (W = 2 copies, or 5 of 6 pieces)", side=("bad", "503: upload refused, nothing recorded", "no"), down_label="yes"),
        S("step", "Chunk recorded in PostgreSQL: nodes, reference count 1"),
        S("step", "After the last chunk: object saved (bucket, key, version, size, ordered chunk list, tags, date)"),
        S("step", "Metadata copied to Elasticsearch for searching"),
        S("end", "201 Created: version, number of chunks, new and deduplicated"),
    ]),
    "read-path": ("Read path: downloading an object", [
        S("start", "Client sends GET /buckets/{bucket}/objects/{key}, optionally with Range: bytes=a-b"),
        S("decision", "Valid API key and under the rate limit?", side=("bad", "401 Unauthorized or 429 Too Many Requests", "no"), down_label="yes"),
        S("step", "Metadata service looks up the object (latest version, or the one asked for) in PostgreSQL"),
        S("decision", "Object exists?", side=("bad", "404 Not Found", "no"), down_label="yes"),
        S("step", "With a Range header, keep only the chunks that overlap bytes a to b"),
        S("step", "For each chunk: look up how it is protected and which nodes hold it"),
        S("decision", "Erasure coded?", side=("note", "Read pieces until 4 good ones, rebuild with Reed-Solomon, check the SHA-256", "yes"), down_label="no"),
        S("step", "Read a copy from a live node, check its SHA-256, and confirm R = 2 nodes hold a good copy"),
        S("decision", "Chunk readable? (read quorum met, or 4 pieces found)", side=("bad", "503: not enough healthy copies", "no"), down_label="yes"),
        S("step", "Stream the bytes (cut to the range) through the gateway to the client"),
        S("end", "200 OK, or 206 Partial Content for a range"),
    ]),
    "repair-path": ("Failure and repair path", [
        S("start", "Every storage node sends a heartbeat every 3 seconds (id, free space, chunk count)"),
        S("decision", "Heartbeat received in the last 10 seconds?", side=("good", "Node healthy, nothing to do", "yes"), down_label="no"),
        S("step", "Node marked DOWN: reads skip it, its objects are shown as degraded"),
        S("decision", "Still down after 20 seconds?", side=("good", "Node came back: copies still valid", "no"), down_label="yes"),
        S("step", "Repair worker (every 5 s) lists the chunks that lost a copy or a piece",
          feed="Scrubber (every hour or on demand): every node re-reads its chunks; corrupted or missing copies are added to the repair list, unused copies deleted"),
        S("decision", "Enough left? (1 good copy, or 4 of 6 pieces)", side=("bad", "Chunk lost: shown in the cluster status", "no"), down_label="yes"),
        S("step", "Read a good copy, or rebuild the missing piece from 4 others (Reed-Solomon)"),
        S("step", "Store it on a healthy node chosen by the ring that does not hold this chunk yet"),
        S("step", "Update the chunk's node list in PostgreSQL"),
        S("end", "Object healthy again: degraded objects back to 0"),
    ]),
}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, (title, steps) in CHARTS.items():
        svg, height = flowchart(title, steps)
        page = f'<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0;background:#fff}}</style></head><body>{svg}</body></html>'
        open(os.path.join(OUT, f"{name}.html"), "w", encoding="utf8").write(page)
        open(os.path.join(OUT, f"{name}.size"), "w").write(str(height))
        print(name, W, height)
