# BMO Implementation Status (evidence-based, 2026-10-16)

Legend: ✅ implemented+tested · 🟡 partial · 📝 specified, not implemented ·
❓ unverified here · ⛔ blocked externally.

The totals in `docs/FINAL_REPORT.md` are previously recorded results, not
a fresh clean-checkout or release-candidate run. Current targeted checks
in this session passed: Python domain 17/17, converter 10/10, real-world
converter 4/4, MCP SDK 6/6, MCP server 4/4, calc 10/10, renderer 6/6,
CLI 5/5, JS SDK 6/6, and 1500/1500 fuzz inputs without escaped
exceptions. Current Rust interop against the cached executable is 8/10;
it reports `UPGRADE_REQUIRED` for DEFLATE and XChaCha. That executable
was built 2026-10-16, before the Rust domain source change on
2026-10-17, so this is not a valid test of current Rust source. A fresh
build is blocked locally by missing `link.exe`. Full log of the earlier
recorded run: docs/FINAL_REPORT.md.

## Format & container (RFC-0001)

| Capability | Status | Evidence |
|---|---|---|
| Magic/header/footer, versions | ✅ | `spec/RFC-0001.md`, `core/rust/src/{header,footer}.rs`, `tests/vectors/*` |
| Typed chunks, object IDs, directory/root index | ✅ | same + `tests/golden/hello.bmo` (829 B, pinned) |
| CRC32C per chunk/header/payload | ✅ | vectors + `test_container.py` tamper tests |
| SHA-256 asset addressing + dedup | ✅ | `graphics.bmo` live `$asset` chain, R6 tests |
| ZSTD compression (id 1) | ✅ Python / ✅ Rust read (`codecs`) | `test_codecs.py` round-trip+tamper; `core/rust/tests/codecs.rs` (ruzstd, corrupt/lying-dlen→CORRUPT); Python-written zstd file validates in Rust CLI (`tests/interop`) |
| DEFLATE (id 2) | ✅ Python / ✅ Rust (`codecs`) | RFC 1950 zlib framing, bounded exact-size decompression, corruption tests |
| AES-256-GCM (id 1) | ✅ Python / ✅ Rust (`codecs`) | Wycheproof official vector in BOTH (+ golden framing, NEED_KEY/AUTH_FAILED discipline); Python-written AES file opens in Rust CLI `--key`, same fail-closed codes (`tests/interop`) |
| ChaCha20-Poly1305 (id 2) | ✅ Python / ✅ Rust (`codecs`) | RFC 8439 §2.8.2 official vector in BOTH; `encrypted.bmo` golden + Python-written ChaCha file decrypt; wrong key/tamper→`AUTH_FAILED`; byte parity Rust CLI `object --key` == Python == manifest hash (`tests/interop`) |
| XChaCha20-Poly1305 (id 3) | ✅ Python / ✅ Rust (`codecs`) | 24-byte nonce framing, keyless `NEED_KEY`, wrong-key/tag `AUTH_FAILED`, audited RustCrypto/libsodium implementations |
| Password Argon2id KDF | ✅ read (kit) | official phc GENKAT trace → 96-block digest manifest, asserted block-for-block + h0 + tag (`test_crypto`); harness cross-checked vs reference C over m/p/t matrix (`test_password`); Rust has no KDF yet (wrapped files → `NEED_KEY`) |
| AES-KW password wrap | ✅ read (kit) / 📝 Rust | RFC 3394 §4.1/§4.6 vectors; `password.bmo` golden end-to-end (KEK/unwrapped-key/plain all pinned, `test_password` 10/10: bounds→`STRUCTURE_INVALID`, wrong pw→`AUTH_FAILED`, keyless→`NEED_KEY`, unknown method→`UPGRADE_REQUIRED`); Rust-side wiring deferred |
| Ed25519 signatures | ✅ vectors+verify | RFC 8032 §7.1 TEST 1..3 + `signed.bmo`; production must use audited libs |
| ECDSA-P256-SHA256 / RSA-PSS-SHA256 signatures | ✅ Python / ✅ Rust (`signatures`) | DER SPKI keys, DER ECDSA signature, PSS MGF1-SHA256/32-byte salt; valid/tampered canonical-hash checks |
| Footer-chain transactions, CoW, recovery scan | ✅ | `test_container.py` (cow_append, footer_recovery, torn-tail) |
| Unknown-critical/ancillary rules | ✅ | `unknown_critical` / `unknown_ancillary` suites |
| Fuzz-clean parsing | ✅ | `tests/fuzz/fuzz_container.py` 1500/1500 clean errors |

## Document model (RFC-0002..0006, 0010)

| Capability | Status | Evidence |
|---|---|---|
| Envelope v1, R1–R6, `$ref`/`$asset`, KIND_INDEX | ✅ | `model.bmo`, `test_model.py`, `core/rust/src/model.rs` |
| Text (paragraph/run/list/style/table, RTL, legacy `text`) | ✅ | `text.bmo`, `table.bmo`, RTL goldens |
| Graphics (canvas/shape/image/group, bmu, paths) | ✅ | `graphics.bmo` (real asset chain) |
| Spreadsheet (cells, formulas+deps contract, merges, numfmt) | ✅ model | `sheet.bmo`; evaluation = separate calc engine ✅ (`calc/`, 10 suites) |
| Presentation (deck/slide/master/layout, transitions, notes, media assets) | ✅ model | `deck.bmo`; RFC-0006 media asset schema and domain checks |
| Rendering profile (SVG/HTML, honesty rule) | ✅ | RFC-0010, `renderer/`, `test_render.py` (deterministic, well-formed) |

## Integration & product

| Capability | Status | Evidence |
|---|---|---|
| Python reference kit (reader/builder/validate) | ✅ | `tests/conformance/bmo_reader.py` + full suite |
| Rust reference core | 🟡 | Current source includes codecs and signature verification, but was not freshly compiled here; MSVC build is blocked by missing `link.exe`, and the GNU/Zig attempt also failed |
| Cross-implementation interop (Rust ↔ Python) | 🟡 | Cached executable: `tests/interop/test_rust_interop.py` 8/10; DEFLATE and XChaCha return `UPGRADE_REQUIRED`. It predates current Rust source; rerun after a successful fresh build |
| JS SDK (container reader) | ✅ | `sdk/js/`, `node --test sdk/js/test.js` 6/6, opens all goldens |
| TypeScript types | ✅ | `sdk/js/bmo.d.ts` matches runtime exports and passes strict TypeScript declaration checking |
| MCP server (9 tools, stdio JSON-RPC) | ✅ + SDK-proven | `mcp/`, `test_mcp.py`, `test_mcp_sdk.py` (real v2 client drives it) |
| CLI (`info/validate/inspect/convert/render/calc`) | ✅ | `cli/bmo.py`, `test_cli.py` |
| Desktop editor (view+edit, CoW saves) | ✅ | `apps/desktop/viewer.py`, `test_viewer.py`, GUI smoke 6/6 |
| Web Studio | ✅ | `apps/web/server.py`, `test_web.py` |
| Windows .exe (PyInstaller one-file, ~11 MB) | ✅ | `dist/BMOViewer.exe`, `tests/test_exe.py` 5/5, association install/uninstall round-trip verified |
| DOCX/XLSX/PPTX/CSV/MD importers | ✅ | crafted 100% round-trips (except documented notes drop); nested DOCX tables round-trip with all 35/35 model elements retained (`compat_matrix.json`: `docx-complex`) |
| PPTX embedded audio/video | ✅ Python path | `pptx-media`: 2/2 media assets retained byte-for-byte through import → BMO asset validation → PPTX export → re-import; model schema is mirrored in Python/Rust validators |
| PDF importer (pdfplumber tables/text + pypdf images) | ✅ | generated-PDF vector; multilingual corpus (Arabic/Hebrew/CJK lines + Arabic-cell table + image, system-font-gated asserts) validates + deterministic (`test_realworld.py` real-pdf-corpus, 2026-10-16) |
| Real Office files | ✅ validity | 3 genuine binaries: no crash, valid BMO, deterministic; images byte-identical; + multilingual PDF corpus (4 scripts detected, 27 objects, matrix entry `real-pdf-corpus`) |
| Oracle verification (real libs both ways) | ✅ | `test_oracles.py`: our exports open in python-docx/openpyxl/python-pptx; imports agree cell-for-cell |
| OOXML package integrity (stdlib) | ✅ | `test_export_fidelity.py` 4/4 — CRCs, XML, content-types, rel targets; caught+fixed missing `rels` Default |
| Benchmarks | ✅ | indexed reads ~12,500× scans, flat at 10× size (`benchmarks/results.json`) |

## Blocked / deferred (honest list)

- Current Rust build and tests on this machine: MSVC compilation is
  blocked by missing `link.exe`; the attempted GNU/Zig build also failed.
  CI is configured but has not been run on GitHub from here. rustfmt/clippy
  components are unavailable.
- Word/Excel/PowerPoint/LibreOffice visual check of exports: structure
  valid + oracle + package integrity green; app eyeball pending (LibreOffice not installed).
- Rust-side AES-KW/Argon2id: password-wrapped files fail closed per-chunk
  (`NEED_KEY`) in the Rust core (no KDF/wrap; dependency-graph risk noted
  in docs/SECURITY_AUDIT.md).
- Random nonce generation for encrypted writes: no writer emits encrypted
  chunks beyond fixed test vectors; RFC-0007 requires a fresh 12-byte
  nonce per payload (verified absent by grep, 2026-10-16).
- Scanned-PDF OCR: out of scope by design (counted, never hallucinated).
- Licensing final sign-off: needs a human lawyer (intent: Apache-2.0 code).
- GitHub Actions workflow updated but never executed on GitHub from here.
- No git commits yet (parent repo `Documents/` has no commits; commit policy undecided).
