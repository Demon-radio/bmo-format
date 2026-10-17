# BMO Roadmap (prioritized, evidence-linked)

## Previously recorded progress (not a release-candidate sign-off)

- RFC-0001..0010 + 10 pinned goldens + conformance across 3 implementations
- Real codecs: ZSTD/DEFLATE + AES-GCM (Wycheproof) + ChaCha20 (RFC vector) +
  XChaCha20 +
  Argon2id (kats) + Ed25519 (RFC vectors) + signed/encrypted goldens
- Converters DOCX/XLSX/PPTX/CSV/MD/PDF with measured matrices + oracle
  verification against the real libraries + genuine-file validity suite
- Calc engine (Excel semantics), MCP + real-SDK interop, desktop editor
  with CoW saves, web studio, Windows exe + file association, CLI, fuzz,
  benchmarks, CI workflow
- Rust source includes optional ZSTD/DEFLATE, AES-GCM, ChaCha/XChaCha,
  and ECDSA/RSA-PSS verification. Current source, codec/signature tests,
  and required Python/Rust interop pass in GitHub Actions. The local
  cached Rust executable still reports `UPGRADE_REQUIRED` for
  DEFLATE/XChaCha and predates current Rust source; a fresh local build is
  blocked by missing MSVC `link.exe`. Earlier recorded interop results
  are not current-source proof.
  Pinned requirements + lock; OOXML package-integrity checks
  (`test_export_fidelity.py`)
- AES-KW password wrap (Argon2id → RFC 3394) in the Python kit: RFC
  3394 §4.1/§4.6 vectors + `password.bmo` golden end-to-end; profile
  bounds/bad envelope fail closed with typed codes (`test_password.py`,
  Python checks / 21 suites. The Argon2id harness DI-addressing bug
  was found, fixed, and re-verified vs the phc GENKAT trace + reference
  C matrix.)

## Next, in order

1. **Rust toolchain + codecs.** Current source, codec/signature tests, and
   interop pass in GitHub Actions. Remaining: restore local `rustfmt` and
   `clippy` verification when the toolchain is available.
2. **AES-KW password wrap.** DONE (Python kit). RFC 3394 §4.1/§4.6
   vectors + `password.bmo` golden (KEK/unwrapped-key/plain pinned);
   wrong password → `AUTH_FAILED`, keyless → `NEED_KEY`, bounds →
   `STRUCTURE_INVALID`, unknown method → `UPGRADE_REQUIRED`. Remaining
   gap: Rust-side KDF/wrap (password-wrapped files fail closed per-chunk
   with `NEED_KEY` there).
3. **Fidelity breadth.** Charts, Excel drawings, real-PDF corpus from
   external producers, DOCX style mapping depth. Each with measured matrix
   updates.
   Completed slices: PPTX audio/video media passthrough as SHA-256 `$asset`
   blobs (2/2 fixture assets retained through import/export/re-import,
   `compat_matrix.json`: `pptx-media`); multilingual PDF corpus (Arabic/
   Hebrew/CJK and Arabic-cell table; deterministic real binary, fonts-gated
   asserts, `real-pdf-corpus` matrix entry); nested DOCX tables preserved
   through import, validation, and export (`docx-complex`: 35/35 model
   elements retained). Remaining:
   external-producer PDF corpus, charts, Excel drawings, and DOCX style
   mapping depth.
4. **Second independent implementation to Accepted.** JS SDK covers the
   container; promote it (types + model layer) or port Python kit to a
   second language, then publish the interop report (RFC-0008 §4).
5. **Packaging & release.** npm SDK alpha `bmo-format-sdk@0.1.0-alpha.0`
   is published. Remaining: SBOM/dependency audit, signed `BMOViewer.exe`
   + versioned GitHub Releases with checksums + release notes,
   and a `pip install bmo` kit. No further publishing without
   explicit authorization for each release (plan §16).
6. **Security process.** `SECURITY.md` contact, advisories workflow,
   dependency audit in CI, second fuzz target (CBOR/model layer).
7. **Standardization prep.** License final sign-off (human lawyer),
   governance doc, extension-registry process, conformance fixtures
   publication. No ISO/W3C claims without authorization.
8. **Apps.** Editor undo UI + history timeline + rollback (bytes already
   support it), print/PDF export, mobile evaluation later.

## Non-goals (explicit)

Scanned-PDF OCR (counted, never hallucinated), macro/script execution,
invented crypto/compression, silent lossy conversion, compatibility
percentages in prose (matrices only).
