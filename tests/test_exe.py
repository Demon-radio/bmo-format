"""Frozen Windows app verification (Task 5 evidence).

Drives dist\\BMOViewer.exe headlessly via --smoke-test / --check.
Skips (exit 0) when the exe is absent; BMO_REQUIRE_APP=1 turns the
skip into failure (release verification runs).
Run: python tests/test_exe.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXE = ROOT / "dist" / "BMOViewer.exe"
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


def run_exe(*args, timeout=180):
    r = subprocess.run([str(EXE), *args], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout or r.stderr).strip()


def t_smoke_test_mode():
    """startup + Tk + loader coherence in the frozen build."""
    for name in ("hello", "text", "sheet", "deck"):
        rc, out = run_exe("--smoke-test", str(GOLDEN / f"{name}.bmo"))
        assert rc == 0, f"{name}: rc={rc} {out}"
        assert out.startswith("SMOKE-OK"), f"{name}: {out}"


def t_check_readable():
    for name in ("hello", "model", "text", "graphics", "sheet", "deck",
                 "table", "signed"):
        rc, out = run_exe("--check", str(GOLDEN / f"{name}.bmo"))
        assert rc == 0, f"{name}: rc={rc} {out}"
        assert out.startswith("CHECK-OK"), f"{name}: {out}"


def t_check_encrypted_fail_closed_message():
    """encrypted.bmo in the FROZEN build must fail with the UPGRADE_REQUIRED
    message, NOT the 'not a valid .bmo file' one. Rationale: the exe bundles
    the reader kit but no `cryptography` library (zero-dep app), so its codec
    registry probe marks enc=2 unsupported -> clean UPGRADE (fail closed).
    The NEED_KEY wording is covered by test_viewer.py (host Python has the
    library; key entry UI is still absent there). UX mapping fixed
    2026-10-15; codec wiring 2026-10-16 kept both paths fail-closed."""
    rc, out = run_exe("--check", str(GOLDEN / "encrypted.bmo"))
    assert rc == 1, f"rc={rc} out={out}"
    assert out.startswith("CHECK-FAIL"), out
    assert "UPGRADE_REQUIRED" in out, out
    assert "valid .bmo" not in out, out


def t_check_invalid_inputs():
    with tempfile.TemporaryDirectory() as td:
        bmo_ext = Path(td) / "renamed.bmo"
        bmo_ext.write_bytes((GOLDEN / "hello.json").read_bytes())
        rc, out = run_exe("--check", str(bmo_ext))
        assert rc == 1 and "CHECK-FAIL" in out, (rc, out)
        assert "valid .bmo" in out, out
        trunc = Path(td) / "truncated.bmo"
        trunc.write_bytes((GOLDEN / "hello.bmo").read_bytes()[:120])
        rc, out = run_exe("--check", str(trunc))
        assert rc == 1 and "CHECK-FAIL" in out, (rc, out)
        rc, out = run_exe("--check", str(Path(td) / "missing.bmo"))
        assert rc == 1 and "CHECK-FAIL" in out, (rc, out)
        assert "read" in out.lower(), out
        empty = Path(td) / "empty.bmo"
        empty.write_bytes(b"")
        rc, out = run_exe("--check", str(empty))
        assert rc == 1 and "CHECK-FAIL" in out, (rc, out)


def t_no_hang_help():
    rc, out = run_exe("--help")
    assert rc == 0 and "usage" in out.lower(), (rc, out)


def main():
    if not EXE.is_file():
        msg = f"SKIP: {EXE} not built (python -m PyInstaller BMOViewer.spec)"
        if os.environ.get("BMO_REQUIRE_APP"):
            print(msg + " — BMO_REQUIRE_APP=1 makes this a failure")
            return 1
        print(msg)
        return 0
    print(f"exe verification against: {EXE}")
    for name, fn in [("smoke_test_mode", t_smoke_test_mode),
                     ("check_readable", t_check_readable),
                     ("check_encrypted_fail_closed_message",
                      t_check_encrypted_fail_closed_message),
                     ("check_invalid_inputs", t_check_invalid_inputs),
                     ("no_hang_help", t_no_hang_help)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
