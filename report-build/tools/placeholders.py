"""Grey placeholder pictures for the report, one per screenshot (rendered by render_images.ps1)."""
import html
import os
import sys

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(base, "tools"))
from report_content import DOC  # noqa: E402

out = os.path.join(base, "placeholders")
os.makedirs(out, exist_ok=True)
for item in DOC:
    if item[0] != "shot":
        continue
    label, caption, what = item[1], item[2], item[3]
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1600px;height:900px;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif}}
.box{{position:absolute;inset:28px;border:6px dashed #9aa3af;border-radius:18px;display:flex;flex-direction:column;
justify-content:center;padding:0 110px;color:#374151}}
.n{{font-size:34px;letter-spacing:3px;color:#6b7280;font-weight:700}}
h1{{font-size:52px;margin:14px 0 26px;color:#111827}}
p{{font-size:32px;line-height:1.4;margin:0 0 26px}}
code{{display:inline-block;font:30px Consolas,monospace;background:#e5e7eb;padding:10px 18px;border-radius:8px;color:#111827}}
.hint{{font-size:24px;color:#6b7280;margin-top:30px}}</style></head><body><div class="box">
<div class="n">SCREENSHOT {html.escape(label)}</div><h1>Replace this image with your screenshot</h1>
<p>{html.escape(caption)}</p><div><code>{html.escape(what)}</code></div>
<div class="hint">In Word: right-click this image, Change Picture, From a File. See the Screenshot Guide for the details.</div>
</div></body></html>"""
    open(os.path.join(out, f"shot-{label}.html"), "w", encoding="utf8").write(page)
print("placeholders written to", out)
