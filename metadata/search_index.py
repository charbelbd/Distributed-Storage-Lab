import json
import os
import threading
import time
from urllib.parse import quote

import requests

ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
INDEX = "objects"
MAPPING = {
    "mappings": {
        "dynamic_templates": [{"tags_as_keywords": {"path_match": "tags.*", "mapping": {"type": "keyword"}}}],
        "properties": {
            "bucket": {"type": "keyword"}, "key": {"type": "keyword"}, "version": {"type": "integer"},
            "size": {"type": "long"}, "chunking": {"type": "keyword"}, "chunks": {"type": "integer"},
            "created_at": {"type": "date"}, "tags": {"type": "object"},
        },
    }
}
state = {"ready": False}
log = print


def doc_id(bucket, key):
    return quote(f"{bucket}/{key}", safe="")


def document(obj):
    return {"bucket": obj["bucket"], "key": obj["key"], "version": obj["version"], "size": obj["size"],
            "chunking": obj["chunking"], "chunks": len(obj["chunk_hashes"]), "tags": obj["tags"],
            "created_at": obj["created_at"].isoformat()}


def setup(logger):
    global log
    log = logger
    threading.Thread(target=wait_and_reindex, daemon=True).start()


def wait_and_reindex():
    from . import db
    while True:
        try:
            requests.get(ES_URL, timeout=2).raise_for_status()
            requests.delete(f"{ES_URL}/{INDEX}", timeout=10)
            requests.put(f"{ES_URL}/{INDEX}", json=MAPPING, timeout=10).raise_for_status()
            with db.connect() as conn:
                objects = db.search_objects(conn, limit=10**9)
            state["ready"] = True
            log(f"Elasticsearch ready, {index_many(objects)} objects indexed")
            return
        except requests.RequestException:
            time.sleep(3)


def index(obj):
    if not state["ready"]:
        return
    try:
        requests.put(f"{ES_URL}/{INDEX}/_doc/{doc_id(obj['bucket'], obj['key'])}", json=document(obj), timeout=5)
    except requests.RequestException:
        state["ready"] = False
        log("Elasticsearch unreachable, search falls back to PostgreSQL until it is back")
        setup(log)


def remove(bucket, key):
    if state["ready"]:
        try:
            requests.delete(f"{ES_URL}/{INDEX}/_doc/{doc_id(bucket, key)}", timeout=5)
        except requests.RequestException:
            pass


def index_many(objects):
    lines = []
    for obj in objects:
        lines.append(json.dumps({"index": {"_index": INDEX, "_id": doc_id(obj["bucket"], obj["key"])}}))
        lines.append(json.dumps(document(obj)))
    for start in range(0, len(lines), 2000):
        body = "\n".join(lines[start:start + 2000]) + "\n"
        requests.post(f"{ES_URL}/_bulk", data=body, headers={"Content-Type": "application/x-ndjson"}, timeout=60)
    return len(objects)
