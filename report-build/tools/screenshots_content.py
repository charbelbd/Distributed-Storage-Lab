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
]
