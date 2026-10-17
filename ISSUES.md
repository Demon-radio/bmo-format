# BMO Engineering Issues (prioritized, living list)

## P0 — correctness / claims
- [x] #1 Rust core: codec and signature implementations added behind
  optional `codecs` / `signatures` features; verification results pending.
  Remaining: MSVC path (`cargo test` with link.exe) unverified on this
  machine; rustfmt/clippy unavailable (interrupted rustup install) — CI
  job covers both feature sets on GitHub runners.
- [x] #15 Encrypted-write nonce generation: verified absent by grep
  (2026-10-16) — no writer emits encrypted chunks beyond fixed vectors;
  every encrypt path takes the nonce as a parameter. Fixed as: recorded
  as a gap in docs/SECURITY_AUDIT.md; a future encrypted writer MUST
  generate a fresh random 12-byte nonce per payload (RFC-0007).
- [ ] #2 Exported Office files never opened in real Word/Excel/PowerPoint
  or LibreOffice (not installed here; see docs/BUILD.md). Mitigated:
  oracle tests (real libs parse our output + agree cell-for-cell) AND
  independent OPC package checks (`tests/converters/test_export_fidelity.py`,
  which caught + fixed a missing `rels` content-type Default, 2026-10-15).

## P1 — capabilities with tests pending
- [x] #3 AES-KW password wrap end-to-end (Python kit): RFC 3394
  §4.1/§4.6 official vectors + `password.bmo` golden (KEK/unwrapped-key/
  plain pinned); bounds→`STRUCTURE_INVALID`, wrong pw→`AUTH_FAILED`,
  keyless→`NEED_KEY`, unknown method→`UPGRADE_REQUIRED`
  (`tests/conformance/test_password.py` 10/10, 2026-10-16). Also fixed
  en route: pure-Python Argon2id harness DI-addressing bug (was indexing
  address words from stream start instead of the segment-local index) —
  re-verified block-for-block vs the phc GENKAT trace, plus cross-check
  vs the reference C implementation over an m/p/t matrix. Remaining:
  Rust-side KDF/wrap (deferred; fails closed per-chunk `NEED_KEY`).
- [ ] #4 Fidelity breadth: charts, Excel drawings, PPTX media,
  style depth, real-PDF corpus. Each needs matrix updates, not prose.
  Slice [x]: multilingual PDF corpus (Arabic/Hebrew/CJK + Arabic-cell
  table + image) green + deterministic (`realworld` matrix entry,
  2026-10-16). Slice [x]: nested DOCX tables preserved through
  import/validation/export (`docx-complex`, 35/35 retained). Remaining:
  corpus from external producers + charts/drawings/media/style depth.
- [ ] #5 `mcp` protocol drift guard: pin `mcp<3` or re-verify on SDK bumps
  (interop test exists: `tests/test_mcp_sdk.py`; requirements.txt pins
  `mcp==2.3.0`).
- [ ] #6 PyMuPDF AGPL boundary: keep import-guarded + optional forever;
  add a license check to CI (requirements.lock.txt labels it test-only).

## P2 — product & release
- [ ] #7 Editor undo UI + history timeline/rollback (bytes already support it).
- [ ] #8 Print/PDF export.
- [ ] #9 Signed releases + checksums + release notes; `pip install bmo`;
  npm publish. NO publishing without authorization.
- [ ] #10 Lockfiles/SBOM + dependency audit in CI.
- [ ] #11 SECURITY contact + disclosure process; legal sign-off on licenses.

## P3 — standard maturity
- [ ] #12 Second implementation to Accepted per RFC-0008 §4 + interop report.
- [ ] #13 Python SDK packaging; WASM evaluation. TypeScript declarations
  are now aligned with the JS runtime and pass strict type checking.
- [ ] #14 No ISO/W3C/endorsement claims without written authorization.
