# Build Notes — Reproducible Toolchains

Status: verified on Windows 10/11 x64, October 2026. Commands below were
run exactly as written on this machine; results are recorded, not assumed.

## Python (3.13.x)

```
python -m pip install -r requirements.txt
python tests/run_all.py          # all portable suites -> "ALL GREEN"
```

## JavaScript

```
node --test sdk/js/test.js
```

## Rust core (`core/rust`)

The reference core builds with `cargo`. Two toolchain paths exist:

### Path A — full MSVC (recommended when available)

Install "Visual Studio Build Tools" (Desktop C++ workload), then:

```
cargo test                        # 32 tests: lib 6 + conformance_hello 4
                                  # + model 7 + roundtrip 3 + domains 12
cargo test --features "codecs signatures"
                                  # codec + ECDSA/RSA-PSS verification tests
```

### Path B — zig/GNU (no MSVC link.exe needed; used on this machine)

`cargo test` on target `x86_64-pc-windows-msvc` requires `link.exe`. When
MSVC is not installed, the GNU target + zig linker works:

```
python -m pip install cargo-zigbuild ziglang
powershell -File core/rust/run_tests.ps1
```

`run_tests.ps1` performs both phases (the dependency-free default build,
then the `codecs` + `signatures` feature build) with the exact steps below:

```
$env:PATH = "<site-packages>\ziglang;$env:PATH"
$env:CARGO_TARGET_DIR = "C:\bmo-target"       # MUST be space-free (see below)
cargo zigbuild --target x86_64-pc-windows-gnu --lib --tests --bins
cargo zigbuild --target x86_64-pc-windows-gnu --lib --tests --bins --features "codecs signatures"
# then run each test binary under $CARGO_TARGET_DIR\...\debug\deps\*-*.exe
```

The feature phase additionally compiles the pure-Rust codec and signature
crates (AES-GCM, ChaCha/XChaCha20-Poly1305, ZSTD, DEFLATE, P-256, RSA and
SHA-256). The core never generates encryption nonces.
The default build keeps zero required dependencies; coded chunks are a
clean `UPGRADE_REQUIRED` signal there.

Known constraints of Path B (all worked around, none silent):

* `cargo-zigbuild` 0.23.4 does not quote the `-o` output path; a space in
  the target path breaks linking (`unrecognized file extension`).
  Workaround: `CARGO_TARGET_DIR` on a space-free path.
* `cargo test` (not `zigbuild`) would invoke `x86_64-w64-mingw32-gcc`,
  which is not installed; use `cargo zigbuild` to build, then run the
  test executables directly; or simply use `run_tests.ps1`.
* The Rust test suite must also pass via Path A before release: the GNU
  build is a development convenience, the MSVC build is the shipped one.

### Cross-implementation interop (Task 2)

```
python tests/interop/test_rust_interop.py
```

Finds the built `bmo` CLI (env `BMO_RUST_BIN`, else common target paths),
then checks: byte-identical `hello` output vs the frozen golden, golden
validation, object-count agreement, keyless fail-closed parity on
`encrypted.bmo`, ChaCha20-Poly1305 golden decrypt byte-parity (Rust CLI
`bmo object --key` == Python == manifest hash), plus codec interop
(Python-written AES file → Rust CLI `validate --key` OK; keyless →
`NEED_KEY`; wrong key → `AUTH_FAILED`; Python-written ZSTD file
validates). Skips (exit 0) when no binary is present (`BMO_REQUIRE_RUST=1`
makes that a hard failure); codec checks additionally skip unless the
binary was built with `--features codecs` (`BMO_REQUIRE_CODECS=1` makes
that a hard failure).

## PyInstaller desktop app

```
python -m PyInstaller BMOViewer.spec
# smoke: dist\BMOViewer.exe --smoke-test
```

## Environment facts (this machine)

* `rustc 1.99.0`, `cargo 1.99.0` (rustup, stable-x86_64-pc-windows-msvc
  toolchain with the `x86_64-pc-windows-gnu` std installed manually from
  static.rust-lang.org).
* No MSVC `link.exe`, no `cl.exe`, no gcc/clang, no LibreOffice.
* `rustfmt`/`clippy` components are unavailable (interrupted rustup
  install); formatting/lints are therefore not part of CI evidence.
