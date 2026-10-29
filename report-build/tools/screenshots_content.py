# Content of "Screenshot Guide.docx". One section per screenshot, grouped by phase.
# New phases are added here as the project grows.

SUBTITLE = "Distributed Systems – Storage Framework Project"
TITLE = ["Screenshot Guide for the ", "Storage Framework Report"]
AUTHORS = "Georgio Abi Aad     Charbel BouDaher"
DATE = "29/10/2026"


def shot(n, title, todo, commands, visible, example=None, note=None):
    out = [("h", f"Screenshot {n} : {title}"), ("label", "What to do:"), ("bullets", todo)]
    if commands:
        out.append(("code", commands))
    out += [("label", "What must be visible:"), ("bullets", visible)]
    if example:
        out += [("label", "It should look like this (from our test run):"), ("code", example)]
    if note:
        out.append(("p", note))
    return out


DOC = [
    ("h", "How to use this guide"),
    ("p", "The lab asks for full-screen screenshots of every phase, each with a paragraph in the report. "
          "Each section below explains one screenshot: what to run, what to do, and what must be visible."),
    ("bullets", [
        "Take every screenshot full screen with PrtScn, as the lab asks.",
        "Save it: paste it into Paint and save it as a PNG, named after its number (for example 01.png).",
        "Maximize the window before each screenshot so long lines are not cut.",
    ]),
    ("p", "Open PowerShell in the project folder and activate the virtual environment before running any command:"),
    ("code", [
        "cd \"$HOME\\Desktop\\Distributed Storage Lab\"",
        ".venv\\Scripts\\activate",
    ]),
    ("p", "When the virtual environment is active, the prompt starts with (.venv)."),
    ("break",),

    ("h", "Phase 1 : Setting up the environment"),
    ("p", "Before these screenshots, connect the project to GitHub once (see Screenshot 2), so every check passes."),

    *shot(1, "Environment check",
          ["Open PowerShell in the project folder and activate the virtual environment (see above).",
           "Run the check script below.",
           "Maximize the window and press PrtScn."],
          ["python scripts/check_env.py"],
          ["Every line marked [OK].",
           "Python 3.11 and the virtual environment path ending in .venv.",
           "Flask and requests installed.",
           "Git repository on branch main, and the GitHub remote address.",
           "Docker engine version and the Kubernetes node desktop-control-plane marked Ready.",
           "The four data folders data\\node1 to data\\node4."],
          ["Python",
           "  [OK] Python version             3.11.9",
           "  [OK] Virtual environment        ...\\Distributed Storage Lab\\.venv",
           "  [OK] package flask              3.1.2",
           "  [OK] package requests           2.32.5",
           "Version control",
           "  [OK] Git                        git version 2.54.0.windows.1",
           "  [OK] Repository                 initialized, branch main",
           "  [OK] Remote (GitHub/GitLab)     https://github.com/<your-user>/distributed-storage-lab.git",
           "Containers",
           "  [OK] Docker engine              29.8.2",
           "  [OK] Kubernetes cluster         desktop-control-plane   Ready   control-plane",
           "Storage node data directories",
           "  [OK] node1                      data\\node1",
           "  ...",
           "  [OK] node4                      data\\node4"]),
    ("break",),

    *shot(2, "The repository on GitHub",
          ["On github.com, click New repository, name it distributed-storage-lab and leave it empty "
           "(no README, no .gitignore). Click Create repository.",
           "In PowerShell, in the project folder, connect and push the project with the two commands below "
           "(use your GitHub user name).",
           "Refresh the repository page in the browser, maximize it and press PrtScn."],
          ["git remote add origin https://github.com/<your-user>/distributed-storage-lab.git",
           "git push -u origin main"],
          ["The repository name and the main branch.",
           "The project files: data, scripts, .gitignore, README.md, requirements.txt.",
           "The first commit message: Set up the project: virtual environment, requirements, node data folders."]),
    ("break",),

    *shot(3, "Docker and Kubernetes running",
          ["Open Docker Desktop from the Start menu or the taskbar.",
           "Wait until the bottom-left corner says Engine running.",
           "Click the gear icon (Settings), then Kubernetes in the left menu.",
           "Maximize the window and press PrtScn."],
          None,
          ["Engine running in the bottom-left corner.",
           "Kubernetes enabled, with the green Kubernetes running status."],
          note="If Kubernetes is not enabled, tick Enable Kubernetes, click Apply and wait a few minutes until it "
               "says running."),
    ("break",),

    ("h", "Phase 2 : The storage node module"),
    *shot(4, "Four storage nodes running in separate containers",
          ["Make sure Docker Desktop says Engine running.",
           "Start everything with the first command (the first build takes a few minutes).",
           "Then run the other two commands.",
           "Maximize the window and press PrtScn when the last command has printed its lines."],
          ["docker compose up --build -d",
           "docker compose ps",
           "docker compose logs metadata"],
          ["The docker compose ps table: postgres, metadata, node1, node2, node3 and node4, all Up, with ports "
           "9001 to 9004 for the nodes and 8000 for the metadata service.",
           "The metadata log lines \"node node1 is UP\" to \"node node4 is UP\": the heartbeats arriving."],
          ["SERVICE    STATUS          PORTS",
           "metadata   Up 14 seconds   0.0.0.0:8000->8000/tcp",
           "node1      Up 13 seconds   0.0.0.0:9001->9000/tcp",
           "node2      Up 13 seconds   0.0.0.0:9002->9000/tcp",
           "node3      Up 13 seconds   0.0.0.0:9003->9000/tcp",
           "node4      Up 14 seconds   0.0.0.0:9004->9000/tcp",
           "postgres   Up 14 seconds   0.0.0.0:5433->5432/tcp",
           "metadata-1  | [metadata] node node4 is UP at http://node4:9000",
           "metadata-1  | [metadata] node node1 is UP at http://node1:9000"]),
    ("break",),

    *shot(5, "Heartbeats received by the metadata service",
          ["With the containers running, open this address in the browser:",
           "Tick Pretty-print if the browser offers it, maximize the window and press PrtScn."],
          ["http://localhost:8000/nodes"],
          ["One entry for each of node1 to node4 with its node_id, free_bytes, chunk_count, alive: true and "
           "seconds_since_heartbeat below 3."]),
    ("break",),

    *shot(6, "Storage node: put, get, head, delete and corruption check",
          ["In PowerShell (virtual environment active), run the test script.",
           "Maximize the window and press PrtScn."],
          ["python scripts/test_node.py"],
          ["PASS for PUT, HEAD, GET and DELETE.",
           "PASS for the PUT with a wrong hash being rejected.",
           "The corruption part: 4 bytes changed inside the node1 container, then the GET refused and the chunk "
           "moved to quarantine.",
           "The node status line at the end (node_id, free_bytes, chunk_count)."],
          ["  PASS  PUT stores the chunk   ({'stored': True, 'size': 200000})",
           "  PASS  HEAD finds it and gives its size   (Content-Length 200000)",
           "  PASS  GET returns the same bytes (hash checked)",
           "  PASS  PUT with a hash that does not match the content is rejected",
           "  PASS  DELETE removes it",
           "Corruption on disk",
           "  changed 4 bytes of chunk 68633f009cb2210f... inside the node1 container",
           "  PASS  GET refuses the corrupted chunk   (chunk is corrupted on this node)",
           "  PASS  the corrupted chunk is moved to quarantine"]),
    ("break",),

    ("h", "Phase 3 : The metadata and placement module"),
    *shot(7, "An upload and its metadata in PostgreSQL",
          ["Upload two files: a small one with fixed chunks and a bigger one with content-defined chunks "
           "(any file of a few MB works for the second, for example a photo or a PDF; replace the path).",
           "Then show what PostgreSQL stores with the last command.",
           "Maximize the window and press PrtScn."],
          ["curl.exe -T README.md http://localhost:8000/objects/demo/README.md",
           "curl.exe -T \"C:\\path\\to\\some-file.pdf\" \"http://localhost:8000/objects/demo/file.pdf?chunking=cdc\"",
           "docker compose exec postgres psql -U storage -c \"SELECT bucket, key, version, size, chunking, "
           "cardinality(chunk_hashes) AS chunks, created_at FROM objects;\" -c \"SELECT left(hash,16) AS chunk, "
           "size, refcount, nodes FROM chunks;\""],
          ["The two upload answers (version, size, number of chunks, new and deduplicated chunks).",
           "The objects table: bucket, key, version, size, chunking, chunk count, creation date.",
           "The chunks table: each chunk with its size, reference count and the 3 nodes chosen by the ring."],
          [" bucket |    key     | version |  size   | chunking | chunks |          created_at",
           " demo   | README.md  |       1 |     558 | fixed    |      1 | 2026-10-29 09:58:05+00",
           " demo   | random.bin |       1 | 9000000 | cdc      |      2 | 2026-10-29 09:58:05+00",
           "",
           "      chunk       |  size   | refcount |        nodes",
           " c1c80b27690e7748 |     558 |        1 | {node3,node1,node4}",
           " 908e896b44d43520 | 4848633 |        1 | {node1,node4,node3}"]),
    ("break",),

    *shot(8, "Deduplication with reference counting",
          ["Run the test script and press PrtScn when it finishes."],
          ["python scripts/test_dedup.py"],
          ["The second upload of the same content: 0 new chunks, all deduplicated.",
           "Each chunk stored once but referenced twice.",
           "After deleting the first object the chunks are still there; after deleting the second they are removed.",
           "Every line PASS."],
          ["  upload original.bin: 3 chunks, 3 new, 0 deduplicated",
           "  upload copy.bin:     3 chunks, 0 new, 3 deduplicated",
           "  PASS  the identical copy stores no new chunk",
           "  PASS  each chunk is stored once but referenced twice   (+3 unique chunks, +6 references)",
           "  PASS  reading copy.bin returns the exact content",
           "  PASS  after deleting original.bin the chunks are still there   (references now +3)",
           "  PASS  copy.bin can still be read",
           "  PASS  after deleting copy.bin the unused chunks are removed   (3 chunks removed from the nodes)"]),
    ("break",),

    *shot(9, "Fixed-size chunking compared with FastCDC",
          ["Run the comparison (it takes about 30 seconds) and press PrtScn when it finishes."],
          ["python scripts/compare_chunking.py"],
          ["The table with both methods: number of chunks, sizes, how many chunks of the edited file are reused, "
           "and the speed.",
           "Fixed 4 MB reuses 0% after a 100-byte insertion, FastCDC reuses almost everything."],
          ["method                 chunks      avg size         min         max       reused    dedup",
           "fixed 4 MB                 50       4015 KB        0 KB     4096 KB       0/51       0.0%",
           "FastCDC (avg ~4 MB)        46       4452 KB      698 KB     7464 KB      45/46      97.8%"]),
    ("break",),

    *shot(10, "Placement: adding and removing a node",
          ["Run the placement test and press PrtScn when it finishes."],
          ["python scripts/test_placement.py"],
          ["The load per node with 4 nodes (about 25% each).",
           "The table for adding node5 and removing node4: the ring moves close to the ideal 20% and 25%, "
           "while hash mod N moves 75 to 80%."],
          ["Load with 4 nodes: node1 23.0%, node2 26.7%, node3 23.2%, node4 27.1%",
           "change                         ideal   ring (primary)   ring (all 3 copies)   hash mod N",
           "add node5 (4 -> 5 nodes)       20.0%            18.5%                 21.5%        79.7%",
           "remove node4 (4 -> 3 nodes)    25.0%            27.1%                 24.5%        75.0%"]),
]
