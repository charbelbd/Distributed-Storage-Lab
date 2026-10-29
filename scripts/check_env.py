import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES = [f"node{i}" for i in range(1, 9)]


def run(command, all_lines=False):
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not output:
        return None
    return output if all_lines else output.splitlines()[0]


def show(label, value, ok=None):
    ok = bool(value) if ok is None else ok
    print(f"  [{'OK' if ok else '!!'}] {label:<26} {value or 'not available'}")


print("Environment check\n")

print("Python")
show("Python version", sys.version.split()[0], sys.version_info >= (3, 10))
in_venv = sys.prefix != sys.base_prefix
show("Virtual environment", sys.prefix if in_venv else None, in_venv)
for package in ["fastapi", "uvicorn", "requests", "httpx", "psycopg", "numpy", "zfec", "pytest"]:
    try:
        from importlib.metadata import version
        show(f"package {package}", version(package))
    except Exception:
        show(f"package {package}", None)

print("\nVersion control")
show("Git", run(["git", "--version"]))
branch = run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"])
show("Repository", branch and f"initialized, branch {branch}")
show("Remote (GitHub/GitLab)", run(["git", "-C", str(ROOT), "remote", "get-url", "origin"]))

print("\nContainers")
show("Docker client", run(["docker", "--version"]))
show("Docker engine", run(["docker", "info", "--format", "{{.ServerVersion}}"]))
show("kubectl", run(["kubectl", "version", "--client"]))
show("Kubernetes cluster", run(["kubectl", "get", "nodes", "--no-headers"]))

print("\nStorage node data (one Docker volume per node, plus a local folder for runs without Docker)")
volumes = (run(["docker", "volume", "ls", "--format", "{{.Name}}", "--filter", "name=node"], all_lines=True) or "").split()
for node in NODES:
    volume = next((v for v in volumes if v.endswith(f"_{node}-data")), None)
    folder = ROOT / "data" / node
    local = f", folder data\\{node}" if folder.is_dir() else ""
    show(node, volume and f"volume {volume}{local}")
