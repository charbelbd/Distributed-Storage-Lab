# Distributed Fault-Tolerant Storage Framework

A small distributed storage system written in Python. Files are cut into chunks,
each chunk is named by its SHA-256 hash, and the chunks are spread over four
storage nodes so that the files survive when a node goes down.

## Setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/check_env.py
```

Each storage node keeps its chunks in its own folder: `data/node1` to `data/node4`.
When the nodes run in Docker, each one gets its own Docker volume instead.
