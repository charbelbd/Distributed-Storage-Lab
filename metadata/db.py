"""PostgreSQL storage for object metadata and chunk reference counts."""
import os

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://storage:storage@localhost:5433/storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    bucket        TEXT        NOT NULL,
    key           TEXT        NOT NULL,
    version       INTEGER     NOT NULL,
    size          BIGINT      NOT NULL,
    chunking      TEXT        NOT NULL,
    chunk_hashes  TEXT[]      NOT NULL,   -- ordered list: the file is these chunks joined together
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bucket, key, version)
);
CREATE TABLE IF NOT EXISTS chunks (
    hash      TEXT    PRIMARY KEY,        -- SHA-256 of the content
    size      INTEGER NOT NULL,
    refcount  INTEGER NOT NULL,           -- how many object versions use this chunk
    nodes     TEXT[]  NOT NULL            -- storage nodes that hold a copy
);
"""


def connect():
    return psycopg.connect(DATABASE_URL, autocommit=False)


def init():
    with connect() as conn:
        conn.execute(SCHEMA)


def chunk_nodes(conn, chunk_hash):
    row = conn.execute("SELECT nodes FROM chunks WHERE hash = %s", (chunk_hash,)).fetchone()
    return row[0] if row else None


def add_reference(conn, chunk_hash, size, nodes):
    """New chunk: insert it with refcount 1. Known chunk: just count one more reference."""
    conn.execute(
        """INSERT INTO chunks (hash, size, refcount, nodes) VALUES (%s, %s, 1, %s)
           ON CONFLICT (hash) DO UPDATE SET refcount = chunks.refcount + 1""",
        (chunk_hash, size, nodes),
    )


def remove_reference(conn, chunk_hash):
    """Count one reference less. Returns the nodes to delete from if nobody uses the chunk anymore."""
    row = conn.execute(
        "UPDATE chunks SET refcount = refcount - 1 WHERE hash = %s RETURNING refcount, nodes", (chunk_hash,)
    ).fetchone()
    if row and row[0] <= 0:
        conn.execute("DELETE FROM chunks WHERE hash = %s", (chunk_hash,))
        return row[1]
    return None


def next_version(conn, bucket, key):
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM objects WHERE bucket = %s AND key = %s", (bucket, key)).fetchone()
    return row[0] + 1


def save_object(conn, bucket, key, version, size, chunking, hashes):
    conn.execute(
        "INSERT INTO objects (bucket, key, version, size, chunking, chunk_hashes) VALUES (%s, %s, %s, %s, %s, %s)",
        (bucket, key, version, size, chunking, hashes),
    )


def get_object(conn, bucket, key, version=None):
    if version:
        query = "SELECT version, size, chunking, chunk_hashes, created_at FROM objects WHERE bucket=%s AND key=%s AND version=%s"
        args = (bucket, key, version)
    else:
        query = "SELECT version, size, chunking, chunk_hashes, created_at FROM objects WHERE bucket=%s AND key=%s ORDER BY version DESC LIMIT 1"
        args = (bucket, key)
    row = conn.execute(query, args).fetchone()
    if not row:
        return None
    return dict(zip(["version", "size", "chunking", "chunk_hashes", "created_at"], row))
