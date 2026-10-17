"""Export fidelity — independent package checks (no Office, no oracles).

LibreOffice is NOT available on the verification machine, so this suite
verifies OOXML exports at the container/schema-lite level with stdlib
only, then prints a per-format matrix. Element-level fidelity is covered
by tests/converters/test_oracles.py (real python-docx/openpyxl/pptx).

Checks per export:
  1. ZIP CRCs valid (zipfile.testzip)
  2. every *.xml / *.rels part is well-formed XML
  3. [Content_Types].xml covers every part (Default ext or Override)
  4. every internal relationship target resolves to a real part
Run: python tests/converters/test_export_fidelity.py
"""
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "converters" / "python"))
from bmo_ooxml import docx, pptx, xlsx  # noqa: E402

CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
passed = failed = 0
matrix = []


def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1


def verify_package(raw: bytes, label: str):
    zf = zipfile.ZipFile(BytesIO(raw))
    bad = zf.testzip()
    assert bad is None, f"{label}: corrupt zip member {bad}"
    names = set(zf.namelist())
    assert "[Content_Types].xml" in names, f"{label}: no content types"
    # 2. XML well-formedness
    xml_parts = [n for n in names if n.endswith((".xml", ".rels"))]
    for n in xml_parts:
        ElementTree.fromstring(zf.read(n))
    # 3. content-type coverage
    ctypes = ElementTree.fromstring(zf.read("[Content_Types].xml"))
    defaults = {d.get("Extension").lower()
                for d in ctypes.findall(f"{CT}Default")}
    overrides = {o.get("PartName") for o in ctypes.findall(f"{CT}Override")}
    for n in names:
        if n == "[Content_Types].xml":
            continue
        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
        assert f"/{n}" in overrides or ext in defaults, (
            f"{label}: part not covered by [Content_Types].xml: {n}")
    # 4. relationship targets resolve
    for n in names:
        if not n.endswith(".rels"):
            continue
        base = n.rsplit("_rels/", 1)[0]
        rels = ElementTree.fromstring(zf.read(n))
        for rel in rels.findall(f"{PR}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            t = rel.get("Target")
            if t.startswith("/"):
                part = t.lstrip("/")
            else:
                part = str(Path(base + t).as_posix())
            import posixpath
            part = posixpath.normpath(part)
            assert part in names, f"{label}: {n} -> missing part {part}"
    return len(names)


def t_docx_package():
    title, envs, _, _ = docx.import_docx(docx.sample_docx())
    out = docx.export_docx(envs, title, [])
    n = verify_package(out, "docx")
    matrix.append(("docx", n, len(out), "PASS"))


def t_xlsx_package():
    title, envs, _, _ = xlsx.import_xlsx(xlsx.sample_xlsx())
    out = xlsx.export_xlsx(envs, title, [])
    n = verify_package(out, "xlsx")
    matrix.append(("xlsx", n, len(out), "PASS"))


def t_pptx_package():
    title, envs, _, _ = pptx.import_pptx(pptx.sample_pptx())
    out = pptx.export_pptx(envs, title, [], {})
    n = verify_package(out, "pptx")
    matrix.append(("pptx", n, len(out), "PASS"))


def t_realworld_exports():
    """Same checks on exports derived from REAL office files."""
    samples = HERE / "samples"
    for name, mod, imp in [("real-images.docx", docx, docx.import_docx),
                           ("real-test1.xlsx", xlsx, xlsx.import_xlsx),
                           ("real-test.pptx", pptx, pptx.import_pptx)]:
        data = (samples / name).read_bytes()
        if mod is pptx:
            title, envs, _, _ = imp(data)
            out = pptx.export_pptx(envs, title, [], {})
        elif mod is xlsx:
            title, envs, _, _ = imp(data)
            out = xlsx.export_xlsx(envs, title, [])
        else:
            title, envs, _, _ = imp(data)
            out = docx.export_docx(envs, title, [])
        verify_package(out, name)


if __name__ == "__main__":
    print("export fidelity (stdlib package checks; LibreOffice absent):")
    for name, fn in [("docx_package", t_docx_package),
                     ("xlsx_package", t_xlsx_package),
                     ("pptx_package", t_pptx_package),
                     ("realworld_exports", t_realworld_exports)]:
        check(name, fn)
    print("\n  format | parts | bytes | package")
    for fmt, n, b, res in matrix:
        print(f"  {fmt:>6} | {n:>5} | {b:>5} | {res}")
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
