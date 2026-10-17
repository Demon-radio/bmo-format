"""Run every portable conformance suite. Exit 0 iff all green.
Usage: python tests/run_all.py
(Rust suites run separately: powershell -File core/rust/run_tests.ps1
covers default and codec/signature feature sets)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = [
    ["python", "tests/vectors/verify_hello.py"],
    ["python", "tests/conformance/test_container.py"],
    ["python", "tests/conformance/test_model.py"],
    ["python", "tests/conformance/test_domains.py"],
    ["python", "tests/conformance/test_crypto.py"],
    ["python", "tests/conformance/test_codecs.py"],
    ["python", "tests/conformance/test_password.py"],
    ["python", "tests/converters/test_converters.py"],
    ["python", "tests/converters/test_export_fidelity.py"],
    ["python", "tests/converters/test_oracles.py"],
    ["python", "tests/converters/test_realworld.py"],
    ["python", "tests/test_mcp_sdk.py"],
    ["python", "tests/interop/test_rust_interop.py"],
    ["python", "tests/test_calc.py"],
    ["python", "tests/test_mcp.py"],
    ["python", "tests/test_viewer.py"],
    ["python", "tests/test_exe.py"],
    ["python", "tests/test_web.py"],
    ["python", "tests/test_render.py"],
    ["python", "tests/test_cli.py"],
    ["python", "tests/fuzz/fuzz_container.py"],
    ["python", "tests/fuzz/fuzz_model.py"],
    ["python", "benchmarks/run.py"],
]
# NOTE: sdk/js runs separately (needs node):  node --test sdk/js/test.js
# NOTE: core/rust runs separately (needs toolchain):
#   powershell -File core/rust/run_tests.ps1   (default + codecs/signatures)

def main() -> int:
    bad = 0
    for cmd in SUITES:
        print(f"=== {' '.join(cmd[1:])} ===", flush=True)
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            bad += 1
            print(f"--- FAILED ({r.returncode}): {cmd[1]}")
    print("=" * 40)
    print("ALL GREEN" if bad == 0 else f"{bad} SUITE(S) FAILED")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
