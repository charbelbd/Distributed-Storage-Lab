# Distributed Fault-Tolerant Storage Framework

A small distributed storage system in Python. Files are cut into chunks, each
chunk is named by its SHA-256 hash, and chunks are spread over 8 storage nodes
with a consistent hashing ring. Each bucket chooses how its data is protected:
3 copies with quorum reads and writes, or Reed-Solomon erasure coding 4+2.
Lost copies are rebuilt automatically when a node dies.

```
 browser / curl / pytest
          |
      gateway :8080        API keys, rate limiting, web UI, search (Elasticsearch)
          |
     metadata :8000        buckets, objects, chunk locations (PostgreSQL), repair worker
          |
   node1 ... node8 :9001-9008   chunks on disk, checked by SHA-256, heartbeat every 3 s
```

## Run

```bash
docker compose up --build -d          # 8 nodes, metadata, gateway, PostgreSQL, Elasticsearch
```

Web UI: http://localhost:8080 (API key `dev-key-123`). API docs: http://localhost:8080/docs

Python environment for the tests and scripts:

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

## API keys

| Key | Limit |
|---|---|
| `dev-key-123` | 20 requests/s |
| `slow-key-789` | 2 requests/s (to show rate limiting) |
| `bench-key-456` | no limit (benchmarks) |

Send it as the header `X-API-Key`, or `?api_key=` for browser downloads.

## Endpoints (gateway)

| Method | Path | What it does |
|---|---|---|
| POST | `/buckets` | create a bucket: `{"name", "redundancy": "replication" or "ec", "replicas", "write_quorum", "read_quorum", "k", "m"}` |
| GET | `/buckets` | list buckets |
| PUT | `/buckets/{b}/objects/{key}` | upload (streamed); tags as `X-Tag-<name>` headers; `?chunking=fixed` or `cdc` |
| GET | `/buckets/{b}/objects/{key}` | download; `Range: bytes=a-b` gives 206; `?version=` for older versions |
| HEAD | `/buckets/{b}/objects/{key}` | size and version |
| DELETE | `/buckets/{b}/objects/{key}` | delete every version |
| GET | `/buckets/{b}/objects?prefix=` | list objects |
| GET | `/meta/{b}/{key}` | metadata: versions, chunks, where each chunk is stored |
| GET | `/search?prefix=&bucket=&min_size=&max_size=&after=&before=&tag=name:value` | search |
| GET | `/status` | nodes, free space, chunks per node, degraded objects, repair progress |

## curl examples (PowerShell, from the `examples` folder)

```powershell
curl.exe -H "X-API-Key: dev-key-123" -H "Content-Type: application/json" -d "@bucket-replication.json" http://localhost:8080/buckets
curl.exe -H "X-API-Key: dev-key-123" -H "Content-Type: application/json" -d "@bucket-ec.json" http://localhost:8080/buckets
curl.exe -H "X-API-Key: dev-key-123" -H "X-Tag-Type: document" -T hello.txt http://localhost:8080/buckets/photos/objects/docs/hello.txt
curl.exe -i -H "X-API-Key: dev-key-123" -H "Range: bytes=0-15" http://localhost:8080/buckets/photos/objects/docs/hello.txt
curl.exe -H "X-API-Key: dev-key-123" "http://localhost:8080/search?tag=type:document"
curl.exe -H "X-API-Key: dev-key-123" http://localhost:8080/status
```

## Tests

| Command | What it checks |
|---|---|
| `python scripts/check_env.py` | the environment is ready |
| `python scripts/test_node.py` | storage node put/get/head/delete, hash check, corruption |
| `python scripts/test_dedup.py` | deduplication and reference counting |
| `python scripts/compare_chunking.py` | fixed 4 MB chunks vs FastCDC |
| `python scripts/test_placement.py` | data moved when a node is added or removed |
| `python scripts/test_redundancy.py` | quorum reads/writes, erasure coding, automatic repair |
| `pytest -v tests/test_api.py` | the gateway API |
