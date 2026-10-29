# Content of "Storage Framework Report.docx" (same layout as our Lab 2 report).
# ("shot", label, caption, what to capture) becomes a grey placeholder picture to replace with the screenshot.

SUBTITLE = "Distributed Systems – Storage Framework Project"
TITLE = ["Distributed Fault-Tolerant ", "Storage Framework"]
AUTHORS = "Georgio Abi Aad     Charbel BouDaher"
DATE = "29/10/2026"
TBD = "[filled in after the benchmark run]"

DOC = [
    ("h", "Introduction"),
    ("p", "We built a small distributed storage system, a bit like a mini Amazon S3. When a file is uploaded, it is cut "
          "into chunks, and every chunk is named by the SHA-256 hash of its content. The chunks are spread over eight "
          "storage nodes with a consistent hashing ring, so that each node gets a fair share and adding or removing a "
          "node only moves a small part of the data."),
    ("p", "Each bucket chooses how its data is protected: three copies of every chunk with quorum reads and writes, or "
          "Reed-Solomon erasure coding with 4 data pieces and 2 parity pieces. When a node dies, a repair worker rebuilds "
          "what was on it, and a scrubber regularly re-reads every chunk to catch silent corruption. Identical chunks are "
          "stored only once. Users reach the system through a gateway with API keys, rate limiting and search, and through "
          "a small web interface."),
    ("p", "The phases below follow the order in which we built the project. Each phase has its screenshots and a "
          "paragraph explaining what we did. The three flowcharts (write path, read path, failure and repair path) are "
          "in their own section near the end."),
    ("h", "Environment"),
    ("table", None, [
        ("Machine", "Windows 11, Intel Core i9-13900HX, Docker Desktop with WSL 2"),
        ("Language", "Python 3.11.9 in a virtual environment (.venv)"),
        ("Web framework", "FastAPI 0.120 with Uvicorn, for the nodes, the metadata service and the gateway"),
        ("Metadata database", "PostgreSQL 16 (Docker image postgres:16-alpine)"),
        ("Search", "Elasticsearch 8.15 (single node)"),
        ("Erasure coding", "zfec 1.6 (Reed-Solomon)"),
        ("Containers", "Docker 29.8 and docker compose, Kubernetes 1.36 (Docker Desktop)"),
        ("Web interface", "Vue.js 3.5 in a single HTML page served by the gateway"),
        ("Tests", "pytest 9.2, curl, and our own test scripts in scripts/"),
        ("Version control", "Git, repository on GitHub (charbelbd/Distributed-Storage-Lab)"),
    ]),
    ("h", "Architecture"),
    ("p", "The system has three kinds of services, each in its own Docker container. Users only talk to the gateway. "
          "The gateway checks the API key and forwards the request to the metadata service, which decides where every "
          "chunk goes and keeps track of everything in PostgreSQL. The storage nodes only store and return chunks."),
    ("table", ("Service", "What it does"), [
        ("gateway (port 8080)", "Entry point for users: API keys, rate limiting, search in Elasticsearch, web UI."),
        ("metadata (port 8000)", "Buckets, objects, chunking, placement, deduplication, repair and scrubbing."),
        ("node1 to node8 (9001-9008)", "Store chunks on their own Docker volume, check every hash, send heartbeats."),
        ("postgres", "Object metadata, chunk locations and reference counts."),
        ("elasticsearch", "A searchable copy of the object metadata."),
    ]),
    ("break",),

    # ---------------- Phase 1 ----------------
    ("h", "Phase 1 : Setting up the environment"),
    ("p", "We installed Python 3.11 and created a virtual environment so the project's packages do not mix with the "
          "rest of the computer. We initialized a Git repository and pushed it to GitHub, and installed Docker Desktop "
          "with its built-in Kubernetes. Each storage node got its own data folder (data/node1 to data/node4 when running "
          "locally), and later its own Docker volume. To check everything at once, we wrote a small script that prints "
          "the version of every tool and whether each part is ready."),
    ("code", [
        "py -3.11 -m venv .venv",
        ".venv\\Scripts\\activate",
        "pip install -r requirements-dev.txt",
        "python scripts/check_env.py",
    ]),
    ("shot", "1", "The environment check: Python, virtual environment, Git and GitHub, Docker, Kubernetes and the node folders.",
     "python scripts/check_env.py, every line [OK]"),
    ("shot", "2", "The project repository on GitHub.", "The repository page on github.com"),
    ("shot", "3", "Docker Desktop with the engine and Kubernetes running.", "Docker Desktop, Settings > Kubernetes"),
    ("break",),

    # ---------------- Phase 2 ----------------
    ("h", "Phase 2 : The storage node module"),
    ("p", "A storage node is a small FastAPI service with four operations on chunks: put, get, head and delete. A chunk "
          "is saved in a file named after its SHA-256 hash. The node does not trust anyone: when a chunk arrives, it "
          "computes the hash itself and refuses the chunk if it does not match its name, and it checks the hash again on "
          "every read. A chunk that has changed on disk is moved to a quarantine folder and never sent back."),
    ("code", [
        "# storage_node/app.py - the hash is checked again on every read",
        "data = path.read_bytes()",
        "if hashlib.sha256(data).hexdigest() != chunk_hash:",
        "    path.replace(DATA_DIR / \"quarantine\" / chunk_hash)",
        "    raise HTTPException(409, \"chunk is corrupted on this node\")",
    ]),
    ("p", "Every three seconds each node sends a heartbeat to the metadata service with its id, its free disk space and "
          "how many chunks it holds. This is how the system knows which nodes are alive. We run eight nodes, each in its "
          "own container with its own Docker volume (the lab asks for at least four; Phase 4 explains why we need eight)."),
    ("shot", "4", "The containers running, and the metadata service receiving the first heartbeat of every node.",
     "docker compose ps and docker compose logs metadata"),
    ("shot", "5", "The heartbeat data of each node as seen by the metadata service.",
     "http://localhost:8080/status?api_key=dev-key-123 in the browser"),
    ("p", "To test a node on its own, we stored, read, checked and deleted a chunk directly on it, sent a chunk whose "
          "content did not match its hash, and finally changed four bytes of a stored chunk inside the container to act "
          "like a failing disk. The node refused to serve the damaged chunk."),
    ("shot", "6", "Testing one storage node: the four operations, the rejected chunk and the corrupted chunk.",
     "python scripts/test_node.py"),
    ("break",),

    # ---------------- Phase 3 ----------------
    ("h", "Phase 3 : The metadata and placement module"),
    ("p", "The metadata service receives uploads, cuts them into chunks and remembers everything in PostgreSQL. For each "
          "object it stores the bucket, the key, the version, the size, the ordered list of chunk hashes, the user tags "
          "and the creation date. Uploading the same key again creates a new version instead of overwriting the old one. "
          "For each chunk it stores where the chunk is and how many objects use it."),
    ("shot", "7", "Two uploads and what PostgreSQL stores about them.", "curl uploads and the psql query"),
    ("p", "Deduplication comes for free from content addressing: two identical chunks have the same hash. Before "
          "sending a chunk to the nodes, the service checks whether it already has it; if so, it only adds one to the "
          "chunk's reference count. When an object is deleted the count goes down, and the chunk is deleted from the "
          "nodes only when no object uses it anymore."),
    ("shot", "8", "Deduplication: the second identical upload stores nothing new, and chunks are only deleted when unused.",
     "python scripts/test_dedup.py"),
    ("p", "We implemented two ways to cut files. Fixed-size chunking cuts every 4 MB; it is very fast but fragile: "
          "inserting a single byte at the start of a file shifts every boundary, so no chunk can be reused. "
          "Content-defined chunking (FastCDC) computes a rolling hash over the last 64 bytes and cuts where the hash "
          "matches a pattern, so the boundaries follow the content. We tested both on a 200 MB file in which we "
          "inserted 100 bytes: fixed chunks reused none of the old chunks, FastCDC reused 45 of 46. FastCDC is slower "
          "(about 14 MB/s against 600 MB/s in our Python version), so fixed chunks stay the default and FastCDC can be "
          "chosen per upload."),
    ("code", [
        "# metadata/chunking.py - a chunk ends where the top bits of the rolling hash are all zero",
        "candidates = np.nonzero((hashes[CDC_MIN:end_small] >> np.uint64(64 - BITS_SMALL)) == 0)[0]",
    ]),
    ("shot", "9", "Fixed-size chunking compared with FastCDC after a small insertion.", "python scripts/compare_chunking.py"),
    ("p", "To decide which nodes hold a chunk, we built a consistent hashing ring. Each node is placed 100 times on the "
          "ring (virtual nodes) so the load is spread evenly, and a chunk goes to the first nodes found clockwise from "
          "its hash. We measured what happens when a node is added or removed: with 8 nodes, adding a ninth moved about "
          "10% of the data and removing one about 11%, close to the ideal 1/N. With simple \"hash modulo number of "
          "nodes\" placement almost 90% of the data would have to move."),
    ("code", [
        "# metadata/ring.py - the first `count` different nodes clockwise from the chunk",
        "start = bisect.bisect(self.points, int(chunk_hash[:16], 16))",
    ]),
    ("shot", "10", "Placement test: data moved when a node is added or removed.", "python scripts/test_placement.py"),
    ("break",),

    # ---------------- Phase 4 ----------------
    ("h", "Phase 4 : Redundancy and recovery"),
    ("p", "Each bucket chooses how its chunks are protected. With replication, every chunk is copied to N nodes "
          "(3 by default). A write succeeds when W nodes have stored it, and a read needs R nodes to confirm they hold "
          "a good copy. We use N = 3, W = 2 and R = 2: because W + R > N, any read always overlaps at least one node of "
          "the last successful write. The service refuses to create a bucket that breaks this rule."),
    ("p", "With erasure coding, a chunk is split into 4 data pieces and 2 parity pieces are computed with Reed-Solomon "
          "(zfec). The 6 pieces go to 6 different nodes, and any 4 of them are enough to rebuild the chunk. It survives "
          "two lost nodes like 3 copies do, but uses 1.5 times the space instead of 3 times."),
    ("code", [
        "# metadata/redundancy.py - any k of the k + m pieces rebuild the chunk",
        "pieces = Encoder(k, k + m).encode(data)",
        "data = Decoder(k, k + m).decode([pieces_by_index[i] for i in indexes], indexes, padding)",
    ]),
    ("p", "To test this we stopped nodes on purpose with docker compose kill, which behaves like a crash. With one copy's "
          "node down a read still worked; with two down it was refused, because the read quorum could not be met. With "
          "seven of the eight nodes down, an upload was refused rather than stored with too few copies. An "
          "erasure-coded chunk was rebuilt with two nodes down, and refused with three."),
    ("shot", "11", "Quorum reads and writes, and erasure coding, with nodes stopped on purpose.",
     "python scripts/test_redundancy.py, parts 1 to 3"),
    ("p", "When a node stays silent for more than 20 seconds, a repair worker that runs every 5 seconds finds every chunk "
          "that had a copy or a piece on it. It reads a good copy, or rebuilds the missing piece from four others, and "
          "stores it on a healthy node chosen by the ring. This is why we run eight nodes: with erasure coding 4+2 on "
          "exactly six nodes, a lost piece has nowhere to go, because every other node already holds a piece of the same "
          "chunk. In our test, 14 chunks were rebuilt and every object was healthy again 20 seconds after the crash."),
    ("shot", "12", "Automatic repair after a node crash.", "python scripts/test_redundancy.py, part 4"),
    ("break",),

    # ---------------- Phase 5 ----------------
    ("h", "Phase 5 : The gateway API"),
    ("p", "The gateway is a separate FastAPI service and the only one users talk to. It checks the API key sent in the "
          "X-API-Key header and limits each key to a number of requests per second with a token bucket; above the limit "
          "it answers 429. Uploads and downloads are streamed through it, so a 1 GB file never sits in memory. Downloads "
          "support byte ranges (Range: bytes=a-b), which only reads the chunks that overlap the range."),
    ("table", ("Endpoint", "What it does"), [
        ("POST /buckets, GET /buckets", "Create a bucket (replication or erasure coding), list buckets"),
        ("PUT /buckets/{b}/objects/{key}", "Upload, streamed; tags as X-Tag-<name> headers"),
        ("GET /buckets/{b}/objects/{key}", "Download, with byte ranges and older versions"),
        ("HEAD, DELETE on an object", "Size and version; delete every version"),
        ("GET /buckets/{b}/objects", "List objects, optionally by key prefix"),
        ("GET /meta/{b}/{key}", "Metadata: versions, chunks and where they are"),
        ("GET /search", "Search by key prefix, size, date and tags"),
        ("GET /status, POST /scrub", "Cluster status; run a scrub now"),
    ]),
    ("p", "We tested the API in two ways. A pytest suite of 20 tests covers authentication, rate limiting, buckets, "
          "upload and download in both protection modes, byte ranges, versions, listing, deletion, search and status. "
          "We also called every endpoint with curl."),
    ("shot", "13", "The pytest suite: 20 tests passed.", "pytest -v tests/test_api.py"),
    ("shot", "14", "The API with curl: missing key, bucket creation, upload, byte range, search and rate limiting.",
     "the curl commands from the guide"),
    ("shot", "15", "The interactive API documentation generated by FastAPI.", "http://localhost:8080/docs"),
    ("break",),

    # ---------------- Phase 6 ----------------
    ("h", "Phase 6 : The web UI"),
    ("p", "We built a simple web interface with Vue.js in one HTML page, served by the gateway, so there is nothing to "
          "build or install. It has three tabs. Objects lists the buckets and their objects and lets the user upload "
          "(with a progress bar and tags), download, delete and look at the chunks of a file. Search queries "
          "Elasticsearch by key prefix, size, date and tags."),
    ("shot", "16", "Browsing and uploading objects, and the chunks of an object with the nodes that hold them.",
     "http://localhost:8080, Objects tab"),
    ("shot", "17", "Searching objects through Elasticsearch.", "Search tab"),
    ("p", "The Cluster tab refreshes every two seconds. It shows the health of every node, how the chunks are spread "
          "across the nodes, how many objects are degraded, and the progress of the repair worker. To see it react, we "
          "crashed a node while the page was open: its card turned red, objects became degraded, and about 20 seconds "
          "later the repair brought everything back to normal."),
    ("shot", "18", "The cluster state with all nodes healthy.", "Cluster tab"),
    ("shot", "19a", "A node down: degraded objects waiting for repair.", "Cluster tab after docker compose kill node2"),
    ("shot", "19b", "The same page after the repair.", "Cluster tab about 20 seconds later"),
    ("break",),

    # ---------------- Phase 7 ----------------
    ("h", "Phase 7 : Data integrity and scrubbing"),
    ("p", "Checking hashes on read only catches corruption in data that someone reads. Data that is rarely read could "
          "rot unnoticed until the other copies are lost too. So every hour, and on demand from the web interface, a "
          "scrubber asks every node to re-read all its chunks and check them against their hashes."),
    ("p", "The metadata service then compares what each node really holds with what it expects. A corrupted or missing "
          "copy is taken off the chunk's node list, so the repair worker rebuilds it on a healthy node. A copy the node "
          "holds but nothing points to (left by a crash during an upload, or by a node that came back after its data was "
          "repaired elsewhere) is deleted, but only if it is older than ten minutes, so an upload in progress is never "
          "touched."),
    ("shot", "20", "The scrubber finding a corrupted copy, a missing copy and an unused copy, and the repair fixing them.",
     "python scripts/test_scrub.py"),
    ("shot", "21", "The scrubbing panel of the web interface.", "Cluster tab, Scrub now"),
    ("break",),

    # ---------------- Phase 8 ----------------
    ("h", "Phase 8 : Testing with two workloads"),
    ("p", "We tested the framework with the two workloads of the lab. The datasets are generated by a script, so anyone "
          "can recreate them: 5,000 small text and JSON files between 1 and 50 KB, and 10 large files between 100 MB "
          "and 1 GB (random data like video, and two disk images where the second is the first with a small change)."),
    ("shot", "22", "Generating the two datasets.", "python scripts/make_datasets.py"),
    ("p", TBD),
    ("shot", "23", "Benchmark of the small-file workload.", "python scripts/benchmark.py small"),
    ("shot", "24", "Benchmark of the large-file workload.", "python scripts/benchmark.py large"),
    ("table", ("Measure", "Result"), [
        ("Small files: upload rate", TBD),
        ("Small files: download rate", TBD),
        ("Small files: upload latency p50 / p95 / p99", TBD),
        ("Large files: upload throughput (replication / erasure coding)", TBD),
        ("Large files: download throughput", TBD),
        ("Disk used per stored byte (replication / erasure coding)", TBD),
        ("Deduplication between the two disk images (fixed / FastCDC)", TBD),
    ]),
    ("break",),

    # ---------------- Flowcharts ----------------
    ("h", "Flowcharts"),
    ("p", "The three flowcharts below show how a request travels through the framework: storing an object, reading it "
          "back, and what happens when a node fails or a chunk is damaged."),
    ("flow", "flowcharts/write-path.png", "Write path: from the upload request to the stored and indexed object."),
    ("flow", "flowcharts/read-path.png", "Read path: from the download request to the streamed bytes."),
    ("flow", "flowcharts/repair-path.png", "Failure and repair path, including the scrubber."),

    ("h", "Problems we ran into"),
    ("bullets", [
        "The metadata service froze on the first heartbeat. The heartbeat handler held a lock and called a function "
        "that took the same lock again. A re-entrant lock fixed it.",
        "Our first FastCDC never found a cut point and every chunk reached the 16 MB maximum. The random table we used "
        "only had odd numbers, so the lowest bit of the hash was always 1 and could never match the pattern. We now "
        "test the top bits of the hash, which also depend on all 64 bytes of the window.",
        "Deduplication first worked across protection modes: uploading a file to an erasure-coded bucket reused the "
        "chunks of the same file in a replicated bucket, so the erasure-coded bucket was never really erasure coded. "
        "Deduplication now happens within each mode.",
        "With six nodes, a failed node's erasure-coded pieces could not be rebuilt anywhere, because every other node "
        "already held a piece of each chunk. We moved to eight nodes.",
        "Search by tag returned too many results. In FastAPI a list parameter must be declared with Query(...), "
        "otherwise it is not read from the URL and the filter is silently ignored.",
    ]),
    ("h", "Limitations"),
    ("bullets", [
        "The metadata service and PostgreSQL are single instances; if they stop, the data is safe on the nodes but "
        "cannot be read until they are back.",
        "An upload is first written to a temporary file on the metadata service before being chunked.",
        "Our FastCDC is written in Python with numpy, so it is much slower than fixed-size chunking.",
        "The API keys and the database password are demonstration values written in docker-compose.yml.",
        "Adding a node changes where new chunks go, but existing chunks are not moved to it automatically.",
    ]),
    ("h", "Conclusion"),
    ("p", "The framework stores files as content-addressed chunks over eight nodes, places them with a consistent hashing "
          "ring that moves only about 1/N of the data when the cluster changes, and keeps them available with either "
          "quorum replication or Reed-Solomon erasure coding. Failed nodes are detected through heartbeats and their data "
          "is rebuilt automatically, every chunk is checked against its SHA-256 on read and by the periodic scrubber, and "
          "identical chunks are stored once. Users reach it through an authenticated, rate-limited API with search, and a "
          "web interface that shows the state of the cluster live."),
    ("h", "References"),
    ("refs", [
        "I. Stoica et al., Chord: A Scalable Peer-to-peer Lookup Service for Internet Applications, SIGCOMM 2001.",
        "G. DeCandia et al., Dynamo: Amazon's Highly Available Key-value Store, SOSP 2007.",
        "W. Xia et al., FastCDC: a Fast and Efficient Content-Defined Chunking Approach for Data Deduplication, USENIX ATC 2016.",
        "I. S. Reed and G. Solomon, Polynomial Codes over Certain Finite Fields, SIAM 1960; zfec library documentation.",
        "FastAPI documentation, fastapi.tiangolo.com; Elasticsearch Reference 8.15, elastic.co.",
    ]),
]
