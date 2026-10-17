"""Calc engine conformance: Excel-compatible evaluation.
Run: python tests/test_calc.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "calc" / "python"))
from bmo_calc.evaluate import evaluate_sheet  # noqa: E402

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

def run(cells, sheets=None, name="S"):
    return evaluate_sheet(cells, sheets, name)

def F(f, v=None, deps=()):
    return {"f": f, "deps": list(deps), "v": v}

def t_arithmetic():
    r = run({"A1": F("2+3*4"), "A2": F("(2+3)*4"), "A3": F("2^3^2"),
             "A4": F("10/4"), "A5": F("-5"), "A6": F("50%")})
    assert r == {"A1": 14, "A2": 20, "A3": 64, "A4": 2.5, "A5": -5, "A6": 0.5}, r

def t_coercion():
    r = run({"A1": {"v": "3"}, "A2": F("A1+4"), "A3": F('A1&"x"'),
             "A4": F("TRUE+1"), "A5": F('"a"+1'), "A6": F("A9+1")})
    assert r["A2"] == 7 and r["A3"] == "3x" and r["A4"] == 2, r
    assert r["A5"] == "#VALUE!", r
    assert r["A6"] == 1, r  # missing cell = blank = 0

def t_compare():
    r = run({"A1": F("1<2"), "A2": F('"a"="A"'), "A3": F('1<"a"'),
             "A4": F('"b">2'), "A5": F("1<>1")})
    assert r == {"A1": True, "A2": True, "A3": True, "A4": True, "A5": False}, r

def t_ranges():
    r = run({"A1": {"v": 1}, "A2": {"v": 2}, "A3": {"v": None},
             "B1": F("SUM(A1:A3)"), "B2": F("AVERAGE(A1:A2)"),
             "B3": F("MIN(A1:A2)"), "B4": F("MAX(A1:A2)"),
             "B5": F("COUNT(A1:A3)"), "B6": F("COUNTA(A1:A3)")})
    assert r["B1"] == 3 and r["B2"] == 1.5, r
    assert (r["B3"], r["B4"], r["B5"], r["B6"]) == (1, 2, 2, 2), r

def t_if_lazy():
    r = run({"A1": F("IF(TRUE,1,1/0)"), "A2": F("IF(FALSE,1/0,2)"),
             "A3": F("IF(1/0,1,2)"), "A4": F("AND(FALSE,1/0)"),
             "A5": F("OR(TRUE,1/0)"), "A6": F('IF("x"="x","y","n")')})
    assert r == {"A1": 1, "A2": 2, "A3": "#DIV/0!", "A4": False,
                 "A5": True, "A6": "y"}, r

def t_strings():
    r = run({"A1": F('LEN("hello")'), "A2": F('UPPER("ab")'),
             "A3": F('LOWER("AB")'), "A4": F('"a"&"b"&3')})
    assert r == {"A1": 5, "A2": "AB", "A3": "ab", "A4": "ab3"}, r

def t_errors():
    r = run({"A1": F("1/0"), "A2": F("SQRT(-1)"), "A3": F("NOSUCHFN(1)"),
             "A4": F("A1:A2"), "A5": F("A1+1")})
    assert r["A1"] == "#DIV/0!" and r["A2"] == "#NUM!", r
    assert r["A3"] == "#NAME?" and r["A4"] == "#VALUE!", r
    assert r["A5"] == "#DIV/0!", r  # error propagates through refs

def t_cycle():
    r = run({"A1": {"v": None, "f": "B1", "deps": ["B1"]},
             "A2": {"v": 1},
             "B1": {"v": None, "f": "A1", "deps": ["A1"]}})
    assert r["A1"] == "#CYCLE!" and r["B1"] == "#CYCLE!" and r["A2"] == 1, r

def t_cross_sheet():
    sheets = {"S1": {"A1": {"v": 10}}, "S2": {"A1": {"v": None, "f": "S1!A1*2", "deps": []}}}
    r = evaluate_sheet(sheets["S2"], sheets, "S2")
    assert r == {"A1": 20}, r

def t_round_excel():
    r = run({"A1": F("ROUND(2.5,0)"), "A2": F("ROUND(2.675,2)"),
             "A3": F("ROUND(-2.5,0)"), "A4": F("MOD(5,3)"), "A5": F("INT(2.9)")})
    assert r == {"A1": 3, "A2": 2.68, "A3": -3, "A4": 2, "A5": 2}, r

if __name__ == "__main__":
    print("calc conformance:")
    for name, fn in [("arithmetic", t_arithmetic), ("coercion", t_coercion),
                     ("compare", t_compare), ("ranges", t_ranges),
                     ("if_lazy", t_if_lazy), ("strings", t_strings),
                     ("errors", t_errors), ("cycle", t_cycle),
                     ("cross_sheet", t_cross_sheet), ("round_excel", t_round_excel)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
