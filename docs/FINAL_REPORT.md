# Final Evidence-Based Report — Master Plan Tasks 1–7

Date: 2026-10-15/16 · Machine: Windows x64, CPython 3.13.1, Node 22.16.0,
rustc/cargo 1.99.0. Every number below is copied from a command run on
this machine; anything not run is listed under "Not verified".

## 1. Reproduce everything

```
python -m pip install -r requirements.txt
python tests/run_all.py                      # 21 suites -> ALL GREEN (144 checks)
node --test sdk/js/test.js                   # 6 pass / 0 fail
powershell -File core/rust/run_tests.ps1     # 32 pass -> RUST SUITE GREEN
python tests/interop/test_rust_interop.py    # 5 pass
python tests/test_exe.py                     # 5 pass (uses dist\BMOViewer.exe)
```

## 2. Task 1 — Rust core build & validation: DONE (GNU/zig path)

* `cargo check --all-targets`: passes (fixed `file.rs:405` deref bug).
* Test execution was blocked by missing MSVC `link.exe`. Solved with the
  zig/GNU path (cargo-zigbuild 0.23.4 + ziglang 0.16.0, GNU std installed
  from static.rust-lang.org), documented in docs/BUILD.md.
* `core/rust/run_tests.ps1` → **32/32 tests green**:
  lib 6, conformance_hello 4, model 7, roundtrip 3, domains 12.
  (Extended to 38 with the codecs feature later — see Addendum.)
* One real test bug found and fixed: `tests/model.rs` used an invalid
  `note` body (plain text instead of `{text: tstr}`) — the domains
  validator correctly rejected it; test fixed, not the validator.
* MSVC path (`cargo test` with link.exe) still unverified here; CI job
  covers it on GitHub runners. rustfmt/clippy unavailable (interrupted
  rustup install; recorded, not hidden).

## 3. Task 2 — Cross-implementation interoperability: DONE

`tests/interop/test_rust_interop.py` — 5/5:

| Check | Result |
|---|---|
| Rust `hello` writer vs frozen golden | **byte-identical** (829 B, sha256 24B2C28F7FBF…67CD) |
| Python kit reads Rust-written bytes | OK (3 entries, ids 1/2/3, 0.1 header) |
| Rust validates all 8 non-codec goldens | all valid; `encrypted.bmo` refused |
| Object counts Rust `info` vs Python reader | identical on all 8 readable goldens |
| `encrypted.bmo` (ChaCha20-Poly1305, enc=2) | **both refuse with the same `UPGRADE_REQUIRED`** |

## 4. Task 3 — Office export fidelity: DONE within environment limits

* LibreOffice is **not installed** on this machine (documented, docs/BUILD.md);
  no visual check in Word/Excel/PowerPoint is possible here.
* New independent evidence: `tests/converters/test_export_fidelity.py` (4/4)
  checks ZIP CRCs, XML well-formedness, `[Content_Types].xml` coverage and
  relationship-target resolution with stdlib only.
* **Real bug found by it and fixed**: every exporter omitted the `rels`
  content-type Default, leaving `word/_rels/document.xml.rels` (and peers)
  uncovered in `[Content_Types].xml` — strict OPC consumers may reject such
  packages. Fixed centrally in `bmo_ooxml/common.py`; oracle + converter +
  realworld suites re-run green (9/9, 6/6, 3/3).
* Semantic fidelity remains backed by real-library oracles (python-docx /
  openpyxl / python-pptx parse our exports; differential imports vs real
  files: 96/96 cells etc.).

## 5. Task 4 — Security & cryptographic scope audit: DONE

`docs/SECURITY_AUDIT.md` (mechanism × implementation × vectors ×
fail-closed table). Highlights:

* Production crypto uses audited libs only (`cryptography`, `zstandard`).
  Pure-Python ed25519/chacha/argon2 exist **only under tests/** — verified
  by grep: no production module imports them.
* Fail-closed evidence: fuzz 1500/1500 clean; wrong-key/tamper →
  `AUTH_FAILED` with no partial plaintext; unknown codecs →
  `UPGRADE_REQUIRED` with identical behavior across implementations.
* Spec-only (NOT implemented): XChaCha, DEFLATE, AES-KW, ECDSA/RSA-PSS.
  ChaCha20-Poly1305: vector-tested primitive + golden, not wired into
  readers. Rust core crypto scope at report time: structure/CRC/bounds
  only (extended 2026-10-16 — see Addendum).

## 6. Task 5 — Windows application verification: DONE

* `tests/test_exe.py` 5/5 against the rebuilt `dist\BMOViewer.exe`:
  smoke on 4 goldens, `--check` on 8 goldens, unsupported-codec message,
  invalid inputs (renamed/truncated/empty/missing → clean exit 1), `--help`.
* **UX bug found and fixed**: encrypted files previously got the generic
  "Not a valid .bmo file — renaming never converts" message; now they get
  a correct UPGRADE_REQUIRED explanation (viewer.py `load_document`).
* File association round-trip verified in HKCU:
  installed → `"…\dist\BMOViewer.exe" "%1"`; uninstall removes both keys;
  reinstall restores the same command.

## 7. Task 6 — CI & reproducible builds: DONE

* `requirements.txt` (direct pins) + `requirements.lock.txt` (full
  installed closure, 46 pins). `pip install --dry-run` resolves all 46.
* CI workflow updated: pinned install before suites; JS command fixed to
  `node --test sdk/js/test.js` (the directory form fails on Node 22.16.0
  here — real failure caught in this session); new `interop` job with
  `BMO_REQUIRE_RUST=1` and new `app` job building the exe and running
  `BMO_REQUIRE_APP=1` verification.
* Caveat: the workflow has **not been executed on GitHub** from this
  machine; only its steps were run locally.

## 8. Task 7 — This report

## Verification matrix (this machine, 2026-10-15/16)

| Suite | Result |
|---|---|
| tests/run_all.py (20 suites) | ALL GREEN — 126 passed + verify_hello + fuzz + bench (pre-addendum) |
| Rust (`run_tests.ps1`) | RUST SUITE GREEN — 32/32 (pre-addendum; now 38 — see below) |
| JS (`node --test sdk/js/test.js`) | 6 pass / 0 fail |
| fuzz_container | 1500 cases, 1500 clean, zero raw exceptions |
| interop | 5/5 at report time (now 7 — see Addendum) |
| exe verification | 5/5 + association round-trip |
| benchmarks | random-access read flat vs linear (perf prints) |

## Addendum (2026-10-16) — Rust codec execution

* Rust core: ZSTD decompression (`ruzstd 0.9.1`) + AES-256-GCM
  decrypt/encrypt (RustCrypto `aes-gcm 0.11.1`) wired behind the
  `codecs` Cargo feature; default build keeps zero required deps and
  cleanly `UPGRADE_REQUIRED`s coded chunks. `getrandom` deliberately
  absent (no host build scripts; the core never generates nonces).
* Evidence re-run after the change:
  * Rust `run_tests.ps1`: **GREEN both phases** — default 32 + codecs 38
    (`tests/codecs.rs` 6/6: Wycheproof AES-GCM vector, NEED_KEY /
    AUTH_FAILED / tamper, zstd roundtrip + corrupt + lying-dlen,
    compress-then-encrypt order, unknown-codec upgrade).
  * `tests/run_all.py`: **ALL GREEN, 128 passed** across 20 suites.
  * Interop **7/7**: Python-written AES file → Rust `bmo validate --key`
    OK (`info` OK; keyless → `NEED_KEY`; wrong key → `AUTH_FAILED`);
    Python-written ZSTD file → Rust validates fully.
  * CLI gained `--key <64-hex>` for `info`/`validate`.
* Newly recorded gap (issue #15): no code generates random nonces yet;
  every encrypt path takes the nonce as a parameter (grep-verified).

## Addendum 2 (2026-10-16) — ChaCha20-Poly1305 (enc=2) reader wiring

* enc=2 is now executable in BOTH readers (RFC 8439): Python kit via
  `cryptography.ChaCha20Poly1305`, Rust core via RustCrypto
  `chacha20poly1305 0.11.0` behind `codecs`. The 0.11.0 line is pinned
  over 0.10.1 because 0.10.1 drags `generic-array` (a host build script)
  into the graph, breaking builds on MSVC-less machines; still no
  `getrandom` (no nonce generation in the core).
* Evidence re-run after the change:
  * Rust `run_tests.ps1`: **GREEN both phases** — default 32 + codecs
    **42** (new in `tests/codecs.rs`: RFC 8439 §2.8.2 official vector,
    Python-computed known-answer ciphertext pin, chunk roundtrip,
    NEED_KEY / AUTH_FAILED / tamper, compress-then-encrypt order,
    enc=3 → `UPGRADE_REQUIRED`).
  * Python `tests/conformance/test_codecs.py` 9/9: ChaCha file roundtrip,
    wrong key/tamper, `encrypted.bmo` golden decrypts through the
    production lib (`plain_sha256` match), enc=3 registry case.
  * Interop **8/8**: `encrypted.bmo` decrypts byte-identically in both —
    Rust CLI `bmo object <file> <oid> --key` stdout == Python reader ==
    frozen manifest `plain_sha256`; keyless both fail closed (`NEED_KEY`
    on codecs builds; `UPGRADE_REQUIRED` on a codecs-less Rust build);
    `tests/run_all.py` ALL GREEN (20 suites); JS 6/6; exe 5/5.
  * CLI gained `bmo object <file.bmo> <object_id> [--key <64-hex>]`
    (binary-safe payload dump — the parity evidence above uses it).
  * Viewer UX: host Python (cryptography present) now reports the
    NEED_KEY wording for encrypted files; the frozen zero-dep exe (no
    `cryptography` bundled) keeps the UPGRADE_REQUIRED wording — both
    fail closed, neither uses the "not a valid .bmo" path.
* Not yet wired (unchanged gaps): XChaCha (enc=3), DEFLATE (comp=2),
  AES-KW wrap, random nonce generation (issue #15), ECDSA/RSA-PSS.

## Addendum 3 (2026-10-16) — Argon2id/AES-KW password wrap + harness fix

* **Real bug found and fixed (test-only harness):** the pure-Python
  Argon2id harness produced a different tag than OpenSSL for some
  (m, p, t) combinations. Root cause: the data-independent address
  stream was consumed from word 0 of each block instead of the
  segment-local index (`address_block.v[i % 128]`, with pass-0/slice-0
  starting at index 2, matching phc `core.c`). Cross-checked against
  `argon2-cffi 25.1.0` (reference C): after the fix the harness matches
  the reference over an m/p/t matrix (m 8..1024, p 1..4, t 1..3).
  The RFC 9106 KAT alone had not caught it (p=4 → seglen 2 → no
  slice-0 consumption).
* **96-block claim now test-backed:** the official phc GENKAT trace
  (`kats/argon2id`, 430888 B, sha256
  `ba05643e504fc5778dda99e2d9f42ebe7d22ebb3923cc719fd591b1b14a8d28d`
  — matches the official `.shasum`) was parsed into a digest manifest
  (`tests/vectors/reference/argon2id_kat_v19_m32_t3_p4.json`: h0 + 96
  block sha256s + tag) and is asserted in `test_crypto.py`
  (`argon2id_blocks`).
* **Password wrap (RFC-0007 §2) implemented in the Python kit** via
  `cryptography` (Argon2id KDF → RFC 3394 AES-KW → content key):
  new `tests/golden/password.bmo` (902 B) + manifest, builder
  `tests/vectors/make_password.py`.
* Evidence re-run after the change:
  * `test_password.py` **10/10**: RFC 3394 §4.1 (primitive) + §4.6
    (through our helpers) official vectors; KDF cross-impl pins
    (harness == OpenSSL == pinned at m=32; profile-scale pins via
    `cryptography`); golden end-to-end (KEK/key/plain sha256 pinned);
    bounds (salt/time/mem/par incl. bool/types) → `STRUCTURE_INVALID`
    before any KDF work; wrong password → `AUTH_FAILED`; keyless →
    `NEED_KEY`; `none` raw key; `kms`/`cert` → `NEED_KEY`; unknown →
    `UPGRADE_REQUIRED`; SECURITY chunk rules (encrypted flag, duplicate,
    garbage CBOR, non-map, trailing bytes) fail closed.
  * `test_crypto.py` 10/10 (incl. new `argon2id_blocks`).
  * `tests/run_all.py`: **ALL GREEN — 143 checks, 21 suites**.
  * Rust `run_tests.ps1`: GREEN both phases (default 32 + codecs 42);
    interop 8/8; JS 6/6; exe 5/5 (all re-run after the change).
* Spec updated: RFC-0007 §5 names the GENKAT trace; change log 0.7.2-alpha.
* Still open (unchanged gaps): Rust-side KDF/key-wrap (wrapped files
  fail closed per-chunk `NEED_KEY` there); random nonce generation
  (issue #15); XChaCha/DEFLATE/ECDSA/RSA-PSS registry-only.

## Addendum 4 (2026-10-16) — Multilingual PDF corpus (fidelity slice)

* `tests/converters/test_realworld.py` gains `real-pdf-corpus`:
  a deterministic 2-page real PDF (built by `pdf_imp.sample_pdf_multilingual`)
  with Latin/Arabic/Hebrew/CJK lines, a ruled table with Arabic cells
  and a PNG image. Asserts (gated on which system fonts exist): import
  title/pages/table/image counts, script detection for all four scripts
  (`Arab`/`Hebr`/`Hani`/`Latn`), full structural+domain validation of the
  built BMO, and import determinism. Recorded in
  `compat_matrix.json` `realworld`: 2 pages, 1 table, 1 image, 27
  objects, all 4 scripts present.
* Arabic extracts (and is stored) as shaped presentation forms in
  visual order — producer behaviour of PyMuPDF; bidi reordering stays a
  renderer concern, counted not corrected (documented in the builder).
* Fixture quirk found while building it (recorded in code): PyMuPDF
  1.28 `new_page()` orphans previously returned Page handles — pages are
  re-fetched via `doc[i]`, and fonts are registered per page.
* Totals after the change: Python **144 checks / 21 suites ALL GREEN**;
  Rust 32+42; interop 8/8; JS 6/6; exe 5/5.

## Not verified (do not claim)

* MSVC-built Rust tests on this machine (CI-only; CI now runs both
  default and `codecs` feature sets); rustfmt/clippy.
* Opening exports in Word/Excel/PowerPoint/LibreOffice.
* GitHub Actions execution (workflow updated, never run on GitHub here).
* No git commits and no packages published in this cycle (no authorization).
* Anything marked spec-only in docs/SECURITY_AUDIT.md.
