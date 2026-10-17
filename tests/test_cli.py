"""CLI conformance: every subcommand on real files via subprocess.
Run: python tests/test_cli.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE.parent / "cli" / "bmo.py"
GOLDEN = HERE / "golden"
passed = failed = 0
TMP = Path(tempfile.mkdtemp(prefix="bmo-cli-"))

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

def run(*args):
    r = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=120)
    return r

def t_info_validate_inspect():
    r = run("info", str(GOLDEN / "hello.bmo"))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["objects"] == 3
    assert run("validate", str(GOLDEN / "sheet.bmo")).returncode == 0
    r = run("inspect", str(GOLDEN / "hello.bmo"))
    assert r.returncode == 0 and "FOOTER" in r.stdout and "0x10" in r.stdout

def t_convert_csv_md():
    (TMP / "s.csv").write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    r = run("convert", str(TMP / "s.csv"), str(TMP / "s.bmo"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert run("validate", str(TMP / "s.bmo")).returncode == 0
    (TMP / "d.md").write_text("# Hi\n\npara **b**.\n", encoding="utf-8")
    assert run("convert", str(TMP / "d.md"), str(TMP / "d.bmo")).returncode == 0
    assert run("validate", str(TMP / "d.bmo")).returncode == 0

def t_convert_bmo_xlsx_roundtrip():
    r = run("convert", str(GOLDEN / "sheet.bmo"), str(TMP / "out.xlsx"), "--from", "bmo", "--to", "xlsx")
    assert r.returncode == 0, r.stdout + r.stderr
    r = run("convert", str(TMP / "out.xlsx"), str(TMP / "back.bmo"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert run("validate", str(TMP / "back.bmo")).returncode == 0

def t_convert_bmo_xlsx_images():
    sys.path.insert(0, str(HERE.parent / "converters" / "python"))
    from bmo_ooxml import container, xlsx

    title, envs, _, assets = xlsx.import_xlsx(xlsx.sample_xlsx_images())
    file_id = bytes(15) + b"\x71"
    raw = container.genesis(
        file_id,
        container.document_meta("spreadsheet", file_id, title,
                                ["model.envelope-v1", "sheet.v1"]),
        [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")})
         for e in envs],
        container.assets_extra(assets))
    source, output = TMP / "images.bmo", TMP / "images.xlsx"
    source.write_bytes(raw)
    r = run("convert", str(source), str(output), "--from", "bmo", "--to", "xlsx")
    assert r.returncode == 0, r.stdout + r.stderr
    _, got, stats, got_assets = xlsx.import_xlsx(output.read_bytes())
    assert stats["images_imported"] == 3, stats
    assert {got_assets[e["body"]["asset"]["$asset"]][1]
            for e in got if e["kind"] == "image"} == {
                blob for _, blob in assets.values()}

def t_calc_cmd():
    r = run("calc", str(GOLDEN / "sheet.bmo"))
    assert r.returncode == 0, r.stdout + r.stderr
    grid = json.loads(r.stdout)
    assert grid["Summary"]["D4"] == 84, grid
    assert grid["Q1"]["A1"] == 0, grid  # cached 2.5 was stale; engine recomputes
    r = run("calc", str(GOLDEN / "sheet.bmo"), "--sheet", "Nope")
    assert r.returncode == 2

def t_render_all_kinds():
    outs = []
    for g, ext in [("text.bmo", "svg"), ("graphics.bmo", "svg"), ("sheet.bmo", "html"),
                   ("deck.bmo", "svg"), ("hello.bmo", "svg")]:
        o = TMP / f"{Path(g).stem}.{ext}"
        r = run("render", str(GOLDEN / g), str(o))
        assert r.returncode == 0, (g, r.stdout + r.stderr)
        outs.append(o)
    assert (TMP / "deck-1.svg").exists() and (TMP / "deck-2.svg").exists()
    assert "<svg" in (TMP / "text.svg").read_text(encoding="utf-8")
    assert "<table" in (TMP / "sheet.html").read_text(encoding="utf-8")

if __name__ == "__main__":
    print("cli conformance:")
    for name, fn in [("info_validate_inspect", t_info_validate_inspect),
                     ("convert_csv_md", t_convert_csv_md),
                     ("convert_bmo_xlsx", t_convert_bmo_xlsx_roundtrip),
                     ("convert_bmo_xlsx_images", t_convert_bmo_xlsx_images),
                     ("calc_cmd", t_calc_cmd),
                     ("render_all", t_render_all_kinds)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
