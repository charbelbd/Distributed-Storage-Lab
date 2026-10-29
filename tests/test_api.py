import hashlib
import os
import time
import uuid

import pytest
import requests

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
KEY = {"X-API-Key": os.environ.get("API_KEY", "dev-key-123")}
RUN = uuid.uuid4().hex[:6]
REP, EC = f"test-rep-{RUN}", f"test-ec-{RUN}"
DATA = os.urandom(9 * 1024 * 1024 + 777)


def api(method, path, **kwargs):
    kwargs["headers"] = {**KEY, **kwargs.get("headers", {})}
    return requests.request(method, GATEWAY + path, timeout=60, **kwargs)


@pytest.fixture(scope="module", autouse=True)
def buckets():
    assert api("POST", "/buckets", json={"name": REP, "redundancy": "replication"}).status_code == 201
    assert api("POST", "/buckets", json={"name": EC, "redundancy": "ec"}).status_code == 201
    yield


def test_missing_api_key_is_rejected():
    assert requests.get(f"{GATEWAY}/buckets").status_code == 401


def test_wrong_api_key_is_rejected():
    assert requests.get(f"{GATEWAY}/buckets", headers={"X-API-Key": "nope"}).status_code == 401


def test_rate_limit_returns_429():
    codes = [requests.get(f"{GATEWAY}/buckets", headers={"X-API-Key": "slow-key-789"}).status_code for _ in range(10)]
    assert 429 in codes and codes[0] == 200
    time.sleep(2)


def test_list_buckets_shows_both_redundancy_types():
    names = {b["name"]: b for b in api("GET", "/buckets").json()}
    assert names[REP]["redundancy"] == "replication" and names[REP]["replicas"] == 3
    assert names[EC]["redundancy"] == "ec" and (names[EC]["k"], names[EC]["m"]) == (4, 2)


def test_duplicate_bucket_is_rejected():
    assert api("POST", "/buckets", json={"name": REP}).status_code == 409


def test_quorum_rule_is_enforced():
    r = api("POST", "/buckets", json={"name": f"bad-{RUN}", "replicas": 3, "write_quorum": 1, "read_quorum": 1})
    assert r.status_code == 400 and "W + R > N" in r.json()["detail"]


@pytest.mark.parametrize("bucket", [REP, EC])
def test_upload_and_download(bucket):
    r = api("PUT", f"/buckets/{bucket}/objects/docs/report.bin", data=DATA, headers={"X-Tag-Project": "lab", "X-Tag-Type": "binary"})
    assert r.status_code == 201, r.text
    assert r.json()["chunks"] == 3 and r.json()["tags"] == {"project": "lab", "type": "binary"}
    down = api("GET", f"/buckets/{bucket}/objects/docs/report.bin")
    assert down.status_code == 200 and hashlib.sha256(down.content).digest() == hashlib.sha256(DATA).digest()


@pytest.mark.parametrize("bucket", [REP, EC])
def test_byte_range_crossing_a_chunk_boundary(bucket):
    start, end = 4 * 1024 * 1024 - 10, 4 * 1024 * 1024 + 9
    r = api("GET", f"/buckets/{bucket}/objects/docs/report.bin", headers={"Range": f"bytes={start}-{end}"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes {start}-{end}/{len(DATA)}"
    assert r.content == DATA[start:end + 1]


def test_suffix_range_returns_the_last_bytes():
    r = api("GET", f"/buckets/{REP}/objects/docs/report.bin", headers={"Range": "bytes=-100"})
    assert r.status_code == 206 and r.content == DATA[-100:]


def test_head_and_metadata():
    head = api("HEAD", f"/buckets/{REP}/objects/docs/report.bin")
    assert head.status_code == 200 and int(head.headers["Content-Length"]) == len(DATA)
    meta = api("GET", f"/meta/{REP}/docs/report.bin").json()
    assert meta["size"] == len(DATA) and len(meta["chunks"]) == 3
    assert all(len(c["nodes"]) == 3 for c in meta["chunks"])


def test_erasure_coded_chunks_have_six_pieces_on_six_nodes():
    meta = api("GET", f"/meta/{EC}/docs/report.bin").json()
    assert all(c["scheme"] == "ec" and len(set(c["nodes"])) == 6 for c in meta["chunks"])


def test_new_upload_creates_a_new_version():
    api("PUT", f"/buckets/{REP}/objects/notes.txt", data=b"first")
    r = api("PUT", f"/buckets/{REP}/objects/notes.txt", data=b"second")
    assert r.json()["version"] == 2
    assert api("GET", f"/buckets/{REP}/objects/notes.txt").content == b"second"
    assert api("GET", f"/buckets/{REP}/objects/notes.txt?version=1").content == b"first"


def test_list_objects_with_prefix():
    keys = [o["key"] for o in api("GET", f"/buckets/{REP}/objects?prefix=docs/").json()]
    assert keys == ["docs/report.bin"]


def test_missing_object_and_bucket():
    assert api("GET", f"/buckets/{REP}/objects/nothing-here").status_code == 404
    assert api("GET", "/buckets/no-such-bucket/objects").status_code == 404


def test_delete_object():
    api("PUT", f"/buckets/{REP}/objects/temp.txt", data=b"bye")
    assert api("DELETE", f"/buckets/{REP}/objects/temp.txt").status_code == 200
    assert api("GET", f"/buckets/{REP}/objects/temp.txt").status_code == 404


def test_search_by_prefix_size_and_tag():
    time.sleep(1.5)
    r = api("GET", "/search", params={"bucket": REP, "prefix": "docs/", "min_size": 1024 * 1024, "tag": "project:lab"}).json()
    assert r["count"] == 1 and r["results"][0]["key"] == "docs/report.bin"
    assert r["engine"] in ("elasticsearch", "postgresql")
    none = api("GET", "/search", params={"bucket": REP, "tag": "project:other"}).json()
    assert none["count"] == 0


def test_search_by_date():
    r = api("GET", "/search", params={"bucket": REP, "after": "2000-01-01", "before": "2000-12-31"}).json()
    assert r["count"] == 0


def test_cluster_status():
    s = api("GET", "/status").json()
    assert len(s["nodes"]) >= 6 and all({"free_bytes", "chunk_count", "alive"} <= n.keys() for n in s["nodes"])
    assert {"degraded_objects", "repair", "dedup_ratio"} <= s.keys()
