import json
import os

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://storage:storage@localhost:5433/storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS buckets (
    name        TEXT        PRIMARY KEY,
    redundancy  TEXT        NOT NULL,          -- 'replication' or 'ec'
    replicas    INTEGER     NOT NULL,          -- N   (replication)
    write_quorum INTEGER    NOT NULL,          -- W
    read_quorum INTEGER     NOT NULL,          -- R
    k           INTEGER     NOT NULL,          -- data pieces    (erasure coding)
    m           INTEGER     NOT NULL,          -- parity pieces  (erasure coding)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS objects (
    bucket        TEXT        NOT NULL REFERENCES buckets(name),
    key           TEXT        NOT NULL,
    version       INTEGER     NOT NULL,
    size          BIGINT      NOT NULL,
    chunking      TEXT        NOT NULL,
    chunk_hashes  TEXT[]      NOT NULL,   -- ordered list: the file is these chunks joined together
    tags          JSONB       NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bucket, key, version)
);
CREATE TABLE IF NOT EXISTS chunks (
    hash      TEXT    PRIMARY KEY,        -- SHA-256 of the content
    size      INTEGER NOT NULL,
    refcount  INTEGER NOT NULL,           -- how many object versions use this chunk
    scheme    TEXT    NOT NULL,           -- 'replication' or 'ec'
    nodes     TEXT[]  NOT NULL,           -- replication: nodes with a copy / ec: node of piece i ('' = missing)
    pieces    TEXT[]  NOT NULL DEFAULT '{}',  -- ec: SHA-256 of piece i
    k         INTEGER NOT NULL DEFAULT 0,
    m         INTEGER NOT NULL DEFAULT 0,
    padding   INTEGER NOT NULL DEFAULT 0,
    target    INTEGER NOT NULL            -- copies (replication) or pieces (ec) it should have
);
"""

BUCKET_FIELDS = ["name", "redundancy", "replicas", "write_quorum", "read_quorum", "k", "m", "created_at"]
CHUNK_FIELDS = ["hash", "size", "refcount", "scheme", "nodes", "pieces", "k", "m", "padding", "target"]
OBJECT_FIELDS = ["bucket", "key", "version", "size", "chunking", "chunk_hashes", "tags", "created_at"]


def connect():
    return psycopg.connect(DATABASE_URL, autocommit=False)


def init():
    with connect() as conn:
        conn.execute(SCHEMA)


def create_bucket(conn, b):
    conn.execute(
        """INSERT INTO buckets (name, redundancy, replicas, write_quorum, read_quorum, k, m)
           VALUES (%(name)s, %(redundancy)s, %(replicas)s, %(write_quorum)s, %(read_quorum)s, %(k)s, %(m)s)""", b)


def get_bucket(conn, name):
    row = conn.execute(f"SELECT {', '.join(BUCKET_FIELDS)} FROM buckets WHERE name = %s", (name,)).fetchone()
    return dict(zip(BUCKET_FIELDS, row)) if row else None


def list_buckets(conn):
    rows = conn.execute(f"""SELECT {', '.join('b.' + f for f in BUCKET_FIELDS)},
                                   COUNT(DISTINCT o.key), COALESCE(SUM(o.size), 0)
                            FROM buckets b LEFT JOIN objects o ON o.bucket = b.name
                            GROUP BY b.name ORDER BY b.name""").fetchall()
    return [{**dict(zip(BUCKET_FIELDS, r)), "objects": r[-2], "bytes": r[-1]} for r in rows]


def get_chunk(conn, chunk_hash):
    row = conn.execute(f"SELECT {', '.join(CHUNK_FIELDS)} FROM chunks WHERE hash = %s", (chunk_hash,)).fetchone()
    return dict(zip(CHUNK_FIELDS, row)) if row else None


def all_chunks(conn):
    rows = conn.execute(f"SELECT {', '.join(CHUNK_FIELDS)} FROM chunks").fetchall()
    return [dict(zip(CHUNK_FIELDS, r)) for r in rows]


def add_chunk(conn, c):
    conn.execute(
        """INSERT INTO chunks (hash, size, refcount, scheme, nodes, pieces, k, m, padding, target)
           VALUES (%(hash)s, %(size)s, 1, %(scheme)s, %(nodes)s, %(pieces)s, %(k)s, %(m)s, %(padding)s, %(target)s)
           ON CONFLICT (hash) DO UPDATE SET refcount = chunks.refcount + 1""", c)


def add_reference(conn, chunk_hash):
    conn.execute("UPDATE chunks SET refcount = refcount + 1 WHERE hash = %s", (chunk_hash,))


def remove_reference(conn, chunk_hash):
    row = conn.execute("UPDATE chunks SET refcount = refcount - 1 WHERE hash = %s RETURNING refcount", (chunk_hash,)).fetchone()
    if row and row[0] <= 0:
        chunk = get_chunk(conn, chunk_hash)
        conn.execute("DELETE FROM chunks WHERE hash = %s", (chunk_hash,))
        return chunk
    return None


def set_chunk_nodes(conn, chunk_hash, nodes):
    conn.execute("UPDATE chunks SET nodes = %s WHERE hash = %s", (nodes, chunk_hash))


def next_version(conn, bucket, key):
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM objects WHERE bucket = %s AND key = %s", (bucket, key)).fetchone()
    return row[0] + 1


def save_object(conn, bucket, key, version, size, chunking, hashes, tags):
    conn.execute(
        """INSERT INTO objects (bucket, key, version, size, chunking, chunk_hashes, tags)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (bucket, key, version, size, chunking, hashes, json.dumps(tags)),
    )


def get_object(conn, bucket, key, version=None):
    query = f"SELECT {', '.join(OBJECT_FIELDS)} FROM objects WHERE bucket=%s AND key=%s"
    if version:
        row = conn.execute(query + " AND version=%s", (bucket, key, version)).fetchone()
    else:
        row = conn.execute(query + " ORDER BY version DESC LIMIT 1", (bucket, key)).fetchone()
    return dict(zip(OBJECT_FIELDS, row)) if row else None


def search_objects(conn, bucket=None, prefix="", min_size=None, max_size=None, after=None, before=None, tags=None, limit=100):
    where, args = ["key LIKE %s"], [prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
    if bucket:
        where.append("bucket = %s"); args.append(bucket)
    if min_size is not None:
        where.append("size >= %s"); args.append(min_size)
    if max_size is not None:
        where.append("size <= %s"); args.append(max_size)
    if after:
        where.append("created_at >= %s"); args.append(after)
    if before:
        where.append("created_at <= %s"); args.append(before)
    if tags:
        where.append("tags @> %s"); args.append(json.dumps(tags))
    rows = conn.execute(
        f"""SELECT * FROM (SELECT DISTINCT ON (bucket, key) {', '.join(OBJECT_FIELDS)} FROM objects
            ORDER BY bucket, key, version DESC) latest WHERE {' AND '.join(where)} ORDER BY bucket, key LIMIT %s""",
        args + [limit]).fetchall()
    return [dict(zip(OBJECT_FIELDS, r)) for r in rows]
