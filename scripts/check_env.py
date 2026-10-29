"""Phase 1 check: is everything we need for the project installed and ready?

Run it with:  python scripts/check_env.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES = ["node1", "node2", "node3", "node4"]


def run(command):
    """Run a command and return its first line of output, or None if it fails."""
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if result.returncode == 0 and output else None


def show(label, value, ok=None):
    ok = bool(value) if ok is None else ok
    print(f"  [{'OK' if ok else '!!'}] {label:<26} {value or 'not available'}")


print("Environment check\n")

print("Python")
show("Python version", sys.version.split()[0], sys.version_info >= (3, 10))
in_venv = sys.prefix != sys.base_prefix
show("Virtual environment", sys.prefix if in_venv else None, in_venv)
for package in ["flask", "requests"]:
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

print("\nStorage node data directories")
for node in NODES:
    folder = ROOT / "data" / node
    show(node, str(folder.relative_to(ROOT)) if folder.is_dir() else None)
