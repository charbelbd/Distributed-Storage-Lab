import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

METADATA_URL = os.environ.get("METADATA_URL", "http://localhost:8000")
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
API_KEYS = dict(item.split("=") for item in os.environ.get("API_KEYS", "dev-key-123=20,bench-key-456=0").split(","))
API_KEYS = {key: float(rate) for key, rate in API_KEYS.items()}
PUBLIC = ("/", "/health", "/docs", "/openapi.json", "/ui")

DOCS = """
The entry point of the distributed storage framework. Every request needs an API key in the
**X-API-Key** header (or **?api_key=** for browser downloads). Keys are rate limited.

* **Buckets**: each bucket is protected by replication (N copies, quorums W and R) or erasure coding (k + m).
* **Objects**: streamed uploads with tags (**X-Tag-name: value**), downloads with **Range: bytes=a-b**, versions.
* **Search**: by key prefix, size, date and tags, in Elasticsearch.
* **Cluster**: node health, chunks per node, degraded objects, repair and scrubbing.
"""
TAGS = [{"name": n, "description": d} for n, d in [
    ("Buckets", "Create and list buckets."),
    ("Objects", "Upload, download, list, delete objects and read their metadata."),
    ("Search", "Find objects by key prefix, size, date and tags."),
    ("Cluster", "State of the storage nodes, repair and scrubbing."),
]]
app = FastAPI(title="Storage gateway", description=DOCS, version="1.0", openapi_tags=TAGS)
client = httpx.AsyncClient(base_url=METADATA_URL, timeout=httpx.Timeout(600, connect=5))
buckets_of_tokens = {}


def log(message):
    print(f"{time.strftime('%H:%M:%S')} [gateway] {message}", flush=True)


def allow(key):
    """Token bucket: each key gets `rate` requests per second, with bursts up to 2x that."""
    rate = API_KEYS[key]
    if rate <= 0:
        return True
    capacity = 2 * rate
    tokens, last = buckets_of_tokens.get(key, (capacity, time.monotonic()))
    now = time.monotonic()
    tokens = min(capacity, tokens + (now - last) * rate)
    if tokens < 1:
        buckets_of_tokens[key] = (tokens, now)
        return False
    buckets_of_tokens[key] = (tokens - 1, now)
    return True


@app.middleware("http")
async def security(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path
    if path not in PUBLIC and not path.startswith("/ui/"):
        key = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if key not in API_KEYS:
            return JSONResponse({"detail": "missing or invalid API key (header X-API-Key)"}, status_code=401)
        if not allow(key):
            return JSONResponse({"detail": "rate limit exceeded, slow down"}, status_code=429,
                                headers={"Retry-After": "1"})
    response = await call_next(request)
    if path not in PUBLIC:
        log(f"{request.method} {path} -> {response.status_code} ({(time.perf_counter() - started) * 1000:.0f} ms)")
    return response


FORWARD_REQUEST = ("content-type", "content-length", "range")
FORWARD_RESPONSE = ("content-type", "content-length", "content-range", "accept-ranges", "x-object-version", "last-modified")


async def forward(request: Request, path: str):
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST or k.lower().startswith("x-tag-")}
    body = request.stream() if request.method in ("PUT", "POST") else None
    try:
        upstream = await client.send(
            client.build_request(request.method, path, params=request.query_params.multi_items(),
                                 headers=headers, content=body),
            stream=True)
    except httpx.HTTPError as error:
        raise HTTPException(503, f"metadata service unreachable: {type(error).__name__}")
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in FORWARD_RESPONSE}
    if request.method == "HEAD":
        await upstream.aclose()
        return Response(status_code=upstream.status_code, headers=out_headers)
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=out_headers,
                             background=BackgroundTask(upstream.aclose))


@app.api_route("/buckets", methods=["GET", "POST"], tags=["Buckets"])
async def buckets(request: Request):
    """GET lists the buckets. POST creates one: {"name", "redundancy": "replication" or "ec", "replicas", "write_quorum", "read_quorum", "k", "m"}."""
    return await forward(request, "/buckets")


@app.api_route("/buckets/{bucket}/objects", methods=["GET"], tags=["Objects"])
async def objects(bucket: str, request: Request):
    """List the objects of a bucket (?prefix= to filter by key prefix)."""
    return await forward(request, f"/buckets/{bucket}/objects")


@app.api_route("/buckets/{bucket}/objects/{key:path}", methods=["GET", "PUT", "HEAD", "DELETE"], tags=["Objects"])
async def single_object(bucket: str, key: str, request: Request):
    """PUT uploads (streamed; tags as X-Tag-name headers; ?chunking=fixed or cdc). GET downloads (Range supported, ?version=). HEAD gives size and version. DELETE removes every version."""
    return await forward(request, f"/buckets/{bucket}/objects/{key}")


@app.get("/meta/{bucket}/{key:path}", tags=["Objects"])
async def metadata(bucket: str, key: str, request: Request):
    """Object metadata: size, versions, tags, and every chunk with the nodes that hold it."""
    return await forward(request, f"/meta/{bucket}/{key}")


@app.get("/status", tags=["Cluster"])
async def status(request: Request):
    """Nodes (health, free space, chunks), degraded objects, repair progress and the last scrub."""
    return await forward(request, "/status")


@app.post("/scrub", tags=["Cluster"])
async def scrub(request: Request):
    """Check every chunk on every node now (it also runs on its own periodically)."""
    return await forward(request, "/scrub")


@app.get("/search", tags=["Search"])
async def search(request: Request, prefix: str = "", bucket: str | None = None, min_size: int | None = None,
                 max_size: int | None = None, after: str | None = None, before: str | None = None,
                 tag: list[str] | None = Query(None, description="name:value, can be repeated"), limit: int = 100):
    """Search objects by key prefix, bucket, size range (bytes), creation date range and tags.
    Uses Elasticsearch, or PostgreSQL if Elasticsearch is down."""
    filters = [{"prefix": {"key": prefix}}] if prefix else []
    if bucket:
        filters.append({"term": {"bucket": bucket}})
    size = {k: v for k, v in (("gte", min_size), ("lte", max_size)) if v is not None}
    if size:
        filters.append({"range": {"size": size}})
    date = {k: v for k, v in (("gte", after), ("lte", before)) if v}
    if date:
        filters.append({"range": {"created_at": date}})
    for t in tag or []:
        name, _, value = t.partition(":")
        filters.append({"term": {f"tags.{name.lower()}": value}})
    query = {"query": {"bool": {"filter": filters}}, "size": limit, "sort": [{"bucket": "asc"}, {"key": "asc"}]}
    try:
        async with httpx.AsyncClient(timeout=5) as es:
            r = await es.post(f"{ES_URL}/objects/_search", json=query)
            r.raise_for_status()
        hits = r.json()["hits"]
        return {"engine": "elasticsearch", "count": hits["total"]["value"], "results": [h["_source"] for h in hits["hits"]]}
    except (httpx.HTTPError, KeyError) as error:
        log(f"Elasticsearch unavailable ({type(error).__name__}), searching PostgreSQL instead")
        return await forward(request, "/search")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "UP"}


UI = Path(__file__).parent / "ui"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(UI / "index.html")


@app.get("/ui/{name}", include_in_schema=False)
def ui_file(name: str):
    path = (UI / name).resolve()
    if path.parent != UI.resolve() or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
