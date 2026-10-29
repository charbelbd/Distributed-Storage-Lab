# Builds a Word document in the same layout as our Lab 2 report (same styles,
# page setup and paragraph formatting), with the body taken from a content module.
#   python report-build/tools/build.py <content module> "<output file>.docx"
import html, os, re, shutil, sys, zipfile, importlib

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # report-build/
sys.path.insert(0, os.path.join(base, "tools"))
content = importlib.import_module(sys.argv[1])
out_path = os.path.abspath(sys.argv[2])
name = os.path.splitext(os.path.basename(out_path))[0]

tpl = os.path.join(base, "template")
work = os.path.join(base, "out-" + name.replace(" ", "_"))
shutil.rmtree(work, ignore_errors=True)
shutil.copytree(tpl, work)

E = lambda s: html.escape(s, quote=False)
CODE_RPR = '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="18"/>'
GREY = '<w:color w:val="444444"/>'


def run(text, rpr=""):
    return f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{E(text)}</w:t></w:r>'


def para(ppr, runs):
    return f'<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>'


def heading(text):
    # Section names get a colon ("Introduction:"), numbered ones do not ("Phase 1 : ...").
    numbered = re.match(r"^(Step|Phase|Screenshot|Part)\b", text)
    return para('<w:keepNext/><w:spacing w:before="280" w:after="120"/><w:rPr><w:u w:val="single"/></w:rPr>',
                run(text if numbered else text + ":", '<w:b/><w:sz w:val="26"/><w:u w:val="single"/>'))


def body(text):
    return para('<w:spacing w:after="160"/>', run(text))


def label(text):
    return para('<w:keepNext/><w:spacing w:after="80"/>', run(text, "<w:b/>"))


def code(lines):
    return "".join(para('<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:ind w:left="288"/>',
                        run(l, CODE_RPR)) for l in lines) + "<w:p/>"


def table(header, rows):
    def cell(text, w, bold):
        rpr = ("<w:b/>" if bold else "") + '<w:sz w:val="20"/>'
        return f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/></w:tcPr><w:p>{run(text, rpr)}</w:p></w:tc>'

    def row(a, b, bold=False):
        return f'<w:tr><w:trPr><w:jc w:val="center"/></w:trPr>{cell(a, 3024, bold)}{cell(b, 5904, bold)}</w:tr>'

    trs = (row(*header, bold=True) if header else "") + "".join(row(a, b) for a, b in rows)
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/><w:jc w:val="center"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
            f'<w:tblGrid><w:gridCol w:w="3024"/><w:gridCol w:w="5904"/></w:tblGrid>{trs}</w:tbl><w:p/>')


def bullets(items):
    return "".join(para('<w:pStyle w:val="ListBullet"/><w:spacing w:after="80"/>', run(t)) for t in items)


def refs(items):
    return "".join(para('<w:spacing w:after="80"/>', run(f"[{i}]  {t}", '<w:sz w:val="20"/>')) for i, t in enumerate(items, 1))


def image(rid, n, cx=5669280, cy=3188970):
    return para('<w:spacing w:before="80" w:after="40"/><w:jc w:val="center"/>',
        f'<w:r><w:rPr><w:noProof/></w:rPr><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{n}" name="Picture {n}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="figure-{n:02d}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


def caption(n, text):
    return para('<w:spacing w:after="280"/><w:jc w:val="center"/>', run(f"Figure {n} – {text}", GREY + '<w:sz w:val="18"/>'))


PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'

parts = [
    para('<w:spacing w:after="80"/><w:jc w:val="center"/>', run(content.SUBTITLE, GREY + '<w:sz w:val="24"/>')),
    para('<w:jc w:val="center"/>', "".join(run(t, '<w:b/><w:sz w:val="40"/>') for t in content.TITLE)),
    para('<w:spacing w:after="80"/><w:jc w:val="center"/>', run(content.AUTHORS, '<w:sz w:val="25"/>')),
    para('<w:spacing w:after="360"/><w:jc w:val="center"/>', run(content.DATE, GREY)),
]

# Remove the Lab 2 pictures; figures are added from the content.
media = os.path.join(work, "word", "media")
shutil.rmtree(media)
os.makedirs(media)
rels_path = os.path.join(work, "word", "_rels", "document.xml.rels")
rels = open(rels_path, encoding="utf8").read()
rels = re.sub(r'<Relationship Id="[^"]+" Type="[^"]+/image" Target="media/[^"]+"/>', "", rels)
new_rels = []

def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def add_figure(src, cap, width_emu=5669280):
    """Insert a picture (scaled to width_emu, keeping its proportions) with a numbered caption."""
    n = len(new_rels) + 1
    w, h = png_size(os.path.join(base, src))
    shutil.copy(os.path.join(base, src), os.path.join(media, f"image{n}.png"))
    rid = f"rIdFig{n}"
    new_rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{n}.png"/>')
    parts.append(image(rid, n, width_emu, int(width_emu * h / w)))
    parts.append(caption(n, cap))


for item in content.DOC:
    kind = item[0]
    if kind == "shot":     # ("shot", label, caption, what): grey placeholder to replace with the screenshot
        add_figure(f"placeholders/shot-{item[1]}.png", item[2])
        continue
    if kind == "flow":     # ("flow", png, caption): a flowchart, a bit narrower than the page
        add_figure(item[1], item[2], width_emu=4680000)
        continue
    if kind == "h": parts.append(heading(item[1]))
    elif kind == "p": parts.append(body(item[1]))
    elif kind == "label": parts.append(label(item[1]))
    elif kind == "code": parts.append(code(item[1]))
    elif kind == "table": parts.append(table(item[1], item[2]))
    elif kind == "break": parts.append(PAGE_BREAK)
    elif kind == "bullets": parts.append(bullets(item[1]))
    elif kind == "refs": parts.append(refs(item[1]))
    elif kind == "figure":  # ("figure", n, "path/to/image.png", "caption", (cx, cy) optional)
        n, src, cap = item[1], item[2], item[3]
        size = item[4] if len(item) > 4 else (5669280, 3188970)
        shutil.copy(os.path.join(base, src), os.path.join(media, f"image{n}.png"))
        rid = f"rIdFig{n}"
        new_rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{n}.png"/>')
        parts.append(image(rid, n, *size))
        parts.append(caption(n, cap))

rels = rels.replace("</Relationships>", "".join(new_rels) + "</Relationships>")
open(rels_path, "w", encoding="utf8").write(rels)

doc_path = os.path.join(work, "word", "document.xml")
doc = open(doc_path, encoding="utf8").read()
head = doc[: doc.index("<w:body>") + len("<w:body>")]
sect = doc[doc.rindex("<w:sectPr"):]
open(doc_path, "w", encoding="utf8").write(head + "".join(parts) + sect)

core = os.path.join(work, "docProps", "core.xml")
c = open(core, encoding="utf8").read()
c = re.sub(r"<dc:title>.*?</dc:title>", f"<dc:title>{E(name)}</dc:title>", c)
open(core, "w", encoding="utf8").write(c)

with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(work, "[Content_Types].xml"), "[Content_Types].xml")
    for folder, _, files in os.walk(work):
        for f in files:
            full = os.path.join(folder, f)
            arc = os.path.relpath(full, work).replace(os.sep, "/")
            if arc != "[Content_Types].xml":
                z.write(full, arc)
shutil.rmtree(work, ignore_errors=True)
print("wrote", out_path)
