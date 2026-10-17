"""Renderer conformance: every golden renders, well-formed, faithful.
Run: python tests/test_render.py
"""
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "renderer" / "python"))
from bmo_render.load import load  # noqa: E402
from bmo_render import svg, sheet_html  # noqa: E402

GOLDEN = HERE / "golden"
passed = failed = 0

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

def t_text_svg():
    m = load((GOLDEN / "text.bmo").read_bytes())
    s1 = svg.render_document(m, "Text Demo")
    s2 = svg.render_document(m, "Text Demo")
    assert s1 == s2, "deterministic"
    ET.fromstring(s1)
    assert "مرحبا بالعالم" in s1 and 'direction="rtl"' in s1
    assert "Hello" in s1 and "font-size=\"32\"" in s1  # Title style 24pt
    assert "•" in s1 or "1." in s1  # list marker
    import re as _re
    texts = _re.findall(r"<text x=\"([^\"]+)\" y=\"([^\"]+)\"", s1)
    assert len(texts) == len(set(texts)), "no two text lines may share a position (overlap bug)"
    assert "<tspan" in s1  # runs flow inside one paragraph line

def t_table_svg():
    m = load((GOLDEN / "table.bmo").read_bytes())
    s = svg.render_document(m)
    ET.fromstring(s)
    for t in ("R1C1", "R1C2", "R2C1", "R2C2"):
        assert t in s, t
    assert s.count("<rect") >= 4  # grid cells

def t_graphics_svg():
    m = load((GOLDEN / "graphics.bmo").read_bytes())
    s = svg.render_canvas_page(m)
    ET.fromstring(s)
    assert "<rect" in s and "<ellipse" in s
    assert "image/png" in s and "db80a10b" in s  # placeholder, honest bytes

def t_deck_slides():
    m = load((GOLDEN / "deck.bmo").read_bytes())
    slides = svg.render_deck(m)
    assert len(slides) == 2, len(slides)
    for s in slides:
        ET.fromstring(s)
    assert "Hello BMO" in slides[0]
    assert "<line" in slides[1]

def t_sheet_html():
    m = load((GOLDEN / "sheet.bmo").read_bytes())
    h1 = sheet_html.render_workbook(m, "Sheet Demo")
    assert h1 == sheet_html.render_workbook(m, "Sheet Demo")
    assert ">42<" in h1 and "Revenue" in h1
    assert 'data-f="A1*2"' in h1 and "colspan=\"2\"" in h1
    assert "#DIV/0!" in h1 and ">TRUE<" in h1

def t_legacy_hello():
    m = load((GOLDEN / "hello.bmo").read_bytes())
    s = svg.render_document(m)
    ET.fromstring(s)
    assert "Hello BMO" in s

if __name__ == "__main__":
    print("renderer conformance:")
    for name, fn in [("text_svg", t_text_svg), ("table_svg", t_table_svg),
                     ("graphics_svg", t_graphics_svg), ("deck_slides", t_deck_slides),
                     ("sheet_html", t_sheet_html), ("legacy_hello", t_legacy_hello)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
