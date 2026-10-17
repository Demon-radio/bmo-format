"""Cross-implementation interop: Rust core CLI vs Python reference kit.
Evidence for the two-implementation rule (spec/ROADMAP.md).

Binary lookup order:
  1. $BMO_RUST_BIN
  2. core/rust/target/debug/bmo.exe           (MSVC build, if present)
  3. $CARGO_TARGET_DIR/x86_64-pc-windows-gnu/debug/bmo.exe
  4. C:\\bmo-target\\x86_64-pc-windows-gnu\\debug\\bmo.exe  (zig workaround)

If no binary is found the suite prints SKIP and exits 0; set
BMO_REQUIRE_RUST=1 to make that a hard failure (CI with toolchain).

Build recipe (Windows, no MSVC link.exe):
  pip install cargo-zigbuild ziglang
  $env:PATH = "<site-packages>\\ziglang;" + $env:PATH
  $env:CARGO_TARGET_DIR = "C:\\bmo-target"   # space-free path required
  cargo zigbuild --target x86_64-pc-windows-gnu --tests --bins --features codecs
  (then run the *-*.exe test binaries under debug/deps/)
Codec interop tests need the binary built WITH `--features codecs`; otherwise
they print SKIP (set BMO_REQUIRE_CODECS=1 to make that a hard failure).
Run: python tests/interop/test_rust_interop.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
sys.path.insert(0, str(ROOT / "tests" / "vectors"))
from bmo_reader import open_file  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
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


def find_rust_bin():
    cands = []
    if os.environ.get("BMO_RUST_BIN"):
        cands.append(Path(os.environ["BMO_RUST_BIN"]))
    cands.append(ROOT / "core" / "rust" / "target" / "debug" / "bmo.exe")
    if os.environ.get("CARGO_TARGET_DIR"):
        cands.append(Path(os.environ["CARGO_TARGET_DIR"]) /
                     "x86_64-pc-windows-gnu" / "debug" / "bmo.exe")
    cands.append(Path(r"C:\bmo-target\x86_64-pc-windows-gnu\debug\bmo.exe"))
    for c in cands:
        if c.is_file():
            return c
    return None


BIN = find_rust_bin()
HAS_CODECS = None  # probed against the binary in main()


def run(*args):
    r = subprocess.run([str(BIN), *args], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def run_bytes(*args):
    """Binary-safe capture (for `bmo object` payload dumps)."""
    r = subprocess.run([str(BIN), *args], capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")


GOLDENS = ["hello.bmo", "model.bmo", "text.bmo", "graphics.bmo",
           "sheet.bmo", "deck.bmo", "table.bmo", "signed.bmo"]


def t_rust_hello_byte_identical():
    """Rust hello writer must reproduce the frozen golden byte-for-byte."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "hello.bmo"
        rc, _, err = run("hello", str(out))
        assert rc == 0, f"bmo hello failed: {err}"
        got = out.read_bytes()
    want = (GOLDEN / "hello.bmo").read_bytes()
    assert got == want, (
        f"byte mismatch: rust={len(got)}B golden={len(want)}B")
    # read back through the Rust CLI as well
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "hello.bmo"
        out.write_bytes(got)
        rc, _, err = run("validate", str(out))
        assert rc == 0, f"rust validate(rust hello) failed: {err}"


def t_python_reads_rust_bytes():
    """Python kit must open + fully walk the Rust-written file."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "hello.bmo"
        rc, _, err = run("hello", str(out))
        assert rc == 0, err
        raw = out.read_bytes()
    f = open_file(raw)
    assert f["header"]["major"] == 0 and f["header"]["minor"] == 1
    assert len(f["entries"]) == 3, f["entries"]
    oids = sorted(e[0] for e in f["entries"])
    assert oids == [1, 2, 3], oids


def t_rust_validates_goldens():
    """All non-codec goldens validate; encrypted fails closed keyless."""
    for name in GOLDENS:
        rc, out, err = run("validate", str(GOLDEN / name))
        assert rc == 0, f"{name}: rc={rc} err={err or out}"
    rc, out, err = run("validate", str(GOLDEN / "encrypted.bmo"))
    assert rc != 0, "encrypted.bmo must NOT validate without a key"
    want = "NEED_KEY" if HAS_CODECS else "UPGRADE_REQUIRED"
    assert want in (err + out), f"want {want}, got: {err or out}"


def t_encrypted_keyless_fail_closed():
    """Keyless open of the ChaCha20 golden must fail closed in BOTH
    implementations — never a silent open, never the 'invalid file' path.
    Python and codecs builds say NEED_KEY; a codecs-less Rust build
    refuses with UPGRADE_REQUIRED."""
    raw = (GOLDEN / "encrypted.bmo").read_bytes()
    try:
        open_file(raw)
        raise AssertionError("python keyless open accepted enc=2!")
    except Exception as e:
        assert "NEED_KEY" in str(e), f"python: {e}"
    want = "NEED_KEY" if HAS_CODECS else "UPGRADE_REQUIRED"
    for sub in ("info", "validate"):
        rc, out, err = run(sub, str(GOLDEN / "encrypted.bmo"))
        assert rc != 0, f"rust {sub} keyless: rc={rc} {out}"
        assert want in (err + out), (
            f"rust {sub} keyless: want {want}, got: {err or out}")


def t_encrypted_golden_parity():
    """encrypted.bmo (ChaCha20-Poly1305, enc=2): Python and Rust produce
    the SAME plaintext bytes, equal to the frozen manifest hash."""
    if not HAS_CODECS:
        return _skip_codec("encrypted_golden_parity")
    import hashlib
    import json
    from bmo_reader import read_chunk
    from make_encrypted import KEY
    raw = (GOLDEN / "encrypted.bmo").read_bytes()
    man = json.loads((GOLDEN / "encrypted.json").read_text())
    f = open_file(raw, KEY)
    oid = f["entries"][0][0]
    _, py_plain, _ = read_chunk(raw, f["by_id"][oid][1], len(raw), KEY)
    rc, out_b, err = run_bytes("object", str(GOLDEN / "encrypted.bmo"),
                               str(oid), "--key", KEY.hex())
    assert rc == 0, f"rust object failed: {err}"
    assert out_b == py_plain, (
        f"byte mismatch: rust={len(out_b)}B python={len(py_plain)}B")
    assert hashlib.sha256(out_b).hexdigest() == man["plain_sha256"], (
        f"rust sha256={hashlib.sha256(out_b).hexdigest()} "
        f"manifest={man['plain_sha256']}")
    # Keyed validate passes end-to-end (decrypt + model rules).
    rc, out, err = run("validate", str(GOLDEN / "encrypted.bmo"), "--key", KEY.hex())
    assert rc == 0, f"rust keyed validate failed: {err or out}"


def t_info_object_counts_match():
    """Rust `info` object count == Python directory entries per golden."""
    for name in GOLDENS:
        raw = (GOLDEN / name).read_bytes()
        rc, out, err = run("info", str(GOLDEN / name))
        assert rc == 0, f"{name}: info rc={rc} err={err}"
        m = re.search(r"Objects: (\d+)", out)
        assert m, f"{name}: no Objects line in: {out!r}"
        rust_n = int(m.group(1))
        py_n = len(open_file(raw)["entries"])
        assert rust_n == py_n, f"{name}: rust={rust_n} python={py_n}"


# --- codec interop (needs a binary built with --features codecs) -----------

def _load_test_codecs():
    import importlib.util
    p = ROOT / "tests" / "conformance" / "test_codecs.py"
    spec = importlib.util.spec_from_file_location("_bmo_test_codecs", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _aes_fixture():
    tc = _load_test_codecs()
    raw, plain = tc.build_aes_file(tc.KEY, tc.NONCE)
    return raw, plain, tc.KEY


def _codec_support():
    raw, _, _ = _aes_fixture()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "aes.bmo"
        p.write_bytes(raw)
        rc, out, err = run("validate", str(p))
    s = err + out
    if "NEED_KEY" in s:
        return True
    if "UPGRADE_REQUIRED" in s:
        return False
    raise AssertionError(f"codec probe: unexpected rc={rc} out={s!r}")


def _skip_codec(name):
    msg = f"SKIP {name}: binary built without --features codecs"
    if os.environ.get("BMO_REQUIRE_CODECS"):
        raise AssertionError(msg + " (BMO_REQUIRE_CODECS=1)")
    print("  " + msg)
    return True


def build_compressed_file(comp, feature, file_id_byte):
    """Same file shape as test_codecs.build_aes_file, with one compressed object."""
    import struct
    from bmo_reader import crc32c, make_chunk
    from make_hello import cbor_encode
    file_id = bytes(15) + bytes([file_id_byte])
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    blob = bytearray(hdr + struct.pack("<I", crc32c(hdr + tail)) + tail)
    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": [feature], "requiredExtensions": [],
        "optionalExtensions": [], "metadata": {}})
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "compress me " * 80}})
    off = len(blob)
    zc = make_chunk(0x0010, 1, 0x000C, 1, plain, comp=comp)
    blob.extend(zc)
    dpay = struct.pack("<IIQ", 1, 1, 1) + struct.pack("<QIIQQ", 1, 0x0010, 0, off, len(zc))
    doff = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    roff = len(blob)
    rc_ = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, doff, len(dc)))
    blob.extend(rc_)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, roff, len(rc_), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")
    return bytes(blob), plain

def build_zstd_file():
    return build_compressed_file(1, "compression.zstd", 11)

def build_deflate_file():
    return build_compressed_file(2, "compression.deflate", 12)


def t_python_writes_aes_rust_reads():
    """Python AES-256-GCM file: Rust CLI opens with --key, fails closed
    identically without a key (NEED_KEY) and with a wrong key (AUTH_FAILED)."""
    if not HAS_CODECS:
        return _skip_codec("python_writes_aes_rust_reads")
    raw, plain, key = _aes_fixture()
    # Python side parity anchors.
    f = open_file(raw, key)
    assert [e[0] for e in f["entries"]] == [1], f["entries"]
    try:
        open_file(raw)
        raise AssertionError("python keyless open accepted an encrypted file!")
    except Exception as e:
        assert "NEED_KEY" in str(e), f"python keyless: {e}"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "aes.bmo"
        p.write_bytes(raw)
        rc, out, err = run("validate", str(p), "--key", key.hex())
        assert rc == 0, f"rust keyed validate failed: {err or out}"
        assert "valid" in out, out
        rc, out, err = run("info", str(p), "--key", key.hex())
        assert rc == 0, f"rust keyed info failed: {err or out}"
        rc, out, err = run("validate", str(p))
        assert rc != 0 and "NEED_KEY" in (err + out), (
            f"rust keyless: rc={rc} {err or out}")
        rc, out, err = run("validate", str(p), "--key", "00" * 32)
        assert rc != 0 and "AUTH_FAILED" in (err + out), (
            f"rust wrong key: rc={rc} {err or out}")


def t_python_writes_zstd_rust_reads():
    """Python ZSTD-compressed chunk: Rust decodes it and validates the
    full model (walk_all_chunks + domain body rules) — exit 0."""
    if not HAS_CODECS:
        return _skip_codec("python_writes_zstd_rust_reads")
    raw, plain = build_zstd_file()
    f = open_file(raw)
    assert [e[0] for e in f["entries"]] == [1], f["entries"]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "zstd.bmo"
        p.write_bytes(raw)
        rc, out, err = run("validate", str(p))
        assert rc == 0, f"rust zstd validate failed: {err or out}"
        rc, out, err = run("info", str(p))
        assert rc == 0, f"rust zstd info failed: {err or out}"
        assert re.search(r"Objects: 1", out), out

def t_python_writes_deflate_rust_reads():
    if not HAS_CODECS:
        return _skip_codec("python_writes_deflate_rust_reads")
    raw, plain = build_deflate_file()
    assert open_file(raw)["entries"][0][0] == 1
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "deflate.bmo"
        p.write_bytes(raw)
        rc, out, err = run("validate", str(p))
        assert rc == 0, f"rust deflate validate failed: {err or out}"
        rc, out, err = run("info", str(p))
        assert rc == 0 and re.search(r"Objects: 1", out), err or out

def t_python_writes_xchacha_rust_reads():
    if not HAS_CODECS:
        return _skip_codec("python_writes_xchacha_rust_reads")
    tc = _load_test_codecs()
    raw, plain = tc.build_aes_file(tc.KEY, tc.XNONCE, enc=3)
    assert open_file(raw, tc.KEY)["entries"][0][0] == 1
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "xchacha.bmo"
        p.write_bytes(raw)
        rc, out, err = run("validate", str(p), "--key", tc.KEY.hex())
        assert rc == 0 and "valid" in out, err or out
        rc, out, err = run("validate", str(p))
        assert rc != 0 and "NEED_KEY" in (err + out), err or out


def main():
    global HAS_CODECS
    if BIN is None:
        msg = ("SKIP: no Rust binary found (build with cargo zigbuild; "
               "see docs/BUILD.md)")
        if os.environ.get("BMO_REQUIRE_RUST"):
            print(msg + " — BMO_REQUIRE_RUST=1 makes this a failure")
            return 1
        print(msg)
        return 0
    print(f"rust interop against: {BIN}")
    HAS_CODECS = _codec_support()
    print(f"codecs feature: {'yes' if HAS_CODECS else 'no (codec tests skip)'}")
    for name, fn in [("rust_hello_byte_identical", t_rust_hello_byte_identical),
                     ("python_reads_rust_bytes", t_python_reads_rust_bytes),
                     ("rust_validates_goldens", t_rust_validates_goldens),
                     ("info_object_counts_match", t_info_object_counts_match),
                     ("encrypted_keyless_fail_closed",
                      t_encrypted_keyless_fail_closed),
                     ("encrypted_golden_parity", t_encrypted_golden_parity),
                     ("python_writes_aes_rust_reads",
                      t_python_writes_aes_rust_reads),
                     ("python_writes_zstd_rust_reads",
                      t_python_writes_zstd_rust_reads),
                     ("python_writes_deflate_rust_reads",
                      t_python_writes_deflate_rust_reads),
                     ("python_writes_xchacha_rust_reads",
                      t_python_writes_xchacha_rust_reads)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
