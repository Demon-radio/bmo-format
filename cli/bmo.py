"""bmo CLI (Python kit): info / validate / inspect / convert / render / calc.
Usage: python cli/bmo.py <command> [args]. Exit 0 on success.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "converters" / "python"))
sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))
sys.path.insert(0, str(HERE.parent / "renderer" / "python"))
from bmo_ooxml import container, csvmd, docx, pptx, xlsx  # noqa: E402
from bmo_ooxml import pdf_imp  # noqa: E402 (import guarded: needs pdfplumber+pypdf)
from bmo_reader import BmoErr, open_file, parse_chunk_hdr, parse_footer  # noqa: E402
from model_check import parse_envelope, validate_structure  # noqa: E402
from domains_check import validate_bodies  # noqa: E402

FORMATS = ("docx", "xlsx", "pptx", "csv", "md", "pdf")

def cmd_info(a):
    raw = Path(a.file).read_bytes()
    f = open_file(raw)
    kinds = {}
    for _, ct, _, _ in f["entries"]:
        kinds[str(ct)] = kinds.get(str(ct), 0) + 1
    print(json.dumps({"file": a.file, "size": len(raw),
                      "format": f"{f['header']['major']}.{f['header']['minor']}",
                      "objects": len(f["entries"]), "txn": f["footer"]["txn"],
                      "encrypted": bool(f["header"]["flags"] & 1),
                      "chunks": kinds}, indent=2))
    return 0

def cmd_validate(a):
    try:
        raw = Path(a.file).read_bytes()
        f = open_file(raw)
        got, ids, tables = [], set(), []
        for oid, ct, fo, tl in f["entries"]:
            ids.add(oid)
            if ct == 0x0010:
                from bmo_reader import read_chunk
                got.append((oid, read_chunk(raw, fo, len(raw))[1]))
        validate_structure(got, ids, tables)
        validate_bodies([parse_envelope(o, p) for o, p in got])
        print("valid (container+structure+domains)")
        return 0
    except (BmoErr, Exception) as e:
        print(f"invalid: {type(e).__name__}: {e}")
        return 1

def cmd_inspect(a):
    raw = Path(a.file).read_bytes()
    print(f"{'off':>8} {'type':>8} {'flags':>6} {'object':>8} {'stored':>8}")
    off, end = 64, len(raw)
    n = 0
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64])
                print(f"{off:8} {'FOOTER':>8}")
                off += 64
                continue
            except BmoErr:
                pass
        try:
            h = parse_chunk_hdr(raw[off:off + 40])
        except BmoErr as e:
            print(f"{off:8} !! {e}")
            break
        print(f"{off:8} {h['type']:#8x} {h['flags']:#6x} {h['oid']:8} {h['slen']:8}")
        off += 40 + h["slen"] + 4
        n += 1
        if n > 100000:
            print("... truncated (100k chunks)")
            break
    return 0

def _to_bmo(fmt, data, title, stem):
    if fmt == "docx":
        t, envs, st, assets = docx.import_docx(data)
        fid = bytes(15) + b"\x30"
        return container.genesis(fid, container.document_meta(
            "document", fid, t, ["model.envelope-v1", "text.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs],
            container.assets_extra(assets)), st
    if fmt == "xlsx":
        t, envs, st, assets = xlsx.import_xlsx(data)
        fid = bytes(15) + b"\x31"
        return container.genesis(fid, container.document_meta(
            "spreadsheet", fid, t, ["model.envelope-v1", "sheet.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs],
            container.assets_extra(assets)), st
    if fmt == "pptx":
        t, envs, st, assets = pptx.import_pptx(data)
        fid = bytes(15) + b"\x32"
        return container.genesis(fid, container.document_meta(
            "presentation", fid, t, ["model.envelope-v1", "text.v1", "graphics.v1", "deck.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs],
            container.assets_extra(assets)), st
    if fmt == "csv":
        t, envs, st = csvmd.import_csv(data.decode("utf-8-sig"), stem)
        fid = bytes(15) + b"\x33"
        return container.genesis(fid, container.document_meta(
            "spreadsheet", fid, t, ["model.envelope-v1", "sheet.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs]), st
    if fmt == "md":
        t, envs, st = csvmd.import_markdown(data.decode("utf-8-sig"), title or stem)
        fid = bytes(15) + b"\x34"
        return container.genesis(fid, container.document_meta(
            "document", fid, t, ["model.envelope-v1", "text.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs]), st
    if fmt == "pdf":
        try:
            t, envs, st, assets = pdf_imp.import_pdf(data)
        except ImportError as e:
            raise ValueError(f"pdf import needs pdfplumber+pypdf ({e})")
        fid = bytes(15) + b"\x35"
        return container.genesis(fid, container.document_meta(
            "document", fid, t, ["model.envelope-v1", "text.v1", "graphics.v1"]),
            [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs],
            container.assets_extra(assets)), st
    raise ValueError(f"cannot import .{fmt}")

def _envs_of(raw):
    from bmo_reader import read_chunk
    f = open_file(raw)
    out = []
    for o, c, fo, t in f["entries"]:
        if c != 0x0010:
            continue
        e = parse_envelope(o, read_chunk(raw, fo, len(raw))[1])
        out.append({"oid": o, "kind": e["kind"], "parent": e["parent"],
                    "index": e["index"], "body": e["body"]})
    return out

def cmd_convert(a):
    src, fmt_in = Path(a.input), (a.from_ or Path(a.input).suffix[1:]).lower()
    fmt_out = (a.to or ("bmo" if fmt_in in FORMATS else None) or "").lower()
    if fmt_in not in FORMATS + ("bmo",) or fmt_out not in FORMATS + ("bmo",):
        print(f"formats: {FORMATS + ('bmo',)}")
        return 2
    data = src.read_bytes()
    if fmt_in == "bmo" and fmt_out == "bmo":
        print("bmo->bmo is a copy; use append flows instead")
        return 2
    if fmt_in == "bmo":
        envs = _envs_of(data)
        approx = []
        if fmt_out == "docx":
            out = docx.export_docx(envs, src.stem, approx)
        elif fmt_out == "xlsx":
            out = xlsx.export_xlsx(envs, src.stem, approx)
        elif fmt_out == "pptx":
            out = pptx.export_pptx(envs, src.stem, approx)
        else:
            print(f"bmo->{fmt_out} not supported yet (import-only format)")
            return 2
        Path(a.output).write_bytes(out)
        print(json.dumps({"wrote": a.output, "bytes": len(out), "approximations": approx}))
        return 0
    if fmt_out != "bmo":
        print("only OOXML->bmo is supported (bmo->OOXML needs a .bmo input)")
        return 2
    raw, stats = _to_bmo(fmt_in, data, a.title or src.stem, src.stem)
    open_file(raw)  # fail fast
    Path(a.output).write_bytes(raw)
    print(json.dumps({"wrote": a.output, "bytes": len(raw), "stats": stats}, default=str))
    return 0

def cmd_render(a):
    from bmo_render.load import load
    from bmo_render import svg, sheet_html
    raw = Path(a.file).read_bytes()
    model = load(raw)
    kinds = {e["kind"] for e in model["envs"]}
    out = Path(a.output)
    if "sheet" in kinds or "workbook" in kinds:
        out.write_text(sheet_html.render_workbook(model, out.stem), encoding="utf-8")
        print(f"wrote {out} (html workbook)")
    elif "slide" in kinds or "deck" in kinds:
        slides = svg.render_deck(model, out.stem)
        base = out.with_suffix("")
        for i, s in enumerate(slides, 1):
            p = Path(f"{base}-{i}.svg")
            p.write_text(s, encoding="utf-8")
        print(f"wrote {len(slides)} slide(s) at {base}-N.svg")
    elif "canvas" in kinds:
        out.write_text(svg.render_canvas_page(model, out.stem), encoding="utf-8")
        print(f"wrote {out} (svg canvas)")
    else:
        out.write_text(svg.render_document(model, out.stem), encoding="utf-8")
        print(f"wrote {out} (svg document)")
    return 0

def cmd_calc(a):
    sys.path.insert(0, str(HERE.parent / "calc" / "python"))
    from bmo_calc.evaluate import evaluate_sheet
    raw = Path(a.file).read_bytes()
    envs = _envs_of(raw)
    sheets = {}
    for e in envs:
        if e["kind"] == "sheet":
            sheets[e["body"].get("name", "?")] = e["body"].get("cells", {})
    if not sheets:
        print("no sheets in file")
        return 2
    if a.sheet and a.sheet not in sheets:
        print(f"no sheet {a.sheet!r} (have: {sorted(sheets)})")
        return 2
    out = {}
    for name, cells in sheets.items():
        if a.sheet and name != a.sheet:
            continue
        out[name] = evaluate_sheet(cells, sheets, name)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(prog="bmo", description="BMO toolkit (Python kit)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("file")
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("validate")
    p.add_argument("file")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("inspect")
    p.add_argument("file")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("convert")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--from", dest="from_", default=None)
    p.add_argument("--to", default=None)
    p.add_argument("--title", default=None)
    p.set_defaults(fn=cmd_convert)
    p = sub.add_parser("render")
    p.add_argument("file")
    p.add_argument("output")
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("calc")
    p.add_argument("file")
    p.add_argument("--sheet", default=None)
    p.set_defaults(fn=cmd_calc)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (BmoErr, ValueError, OSError) as e:
        print(f"error: {type(e).__name__}: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
