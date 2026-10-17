# BMO Architecture (as built)

## Layering (dependencies point down only)

```text
apps: desktop editor (tkinter) · web studio (stdlib http) · cli
mcp:  stdio JSON-RPC server (9 tools) — speaks to any MCP client
sdk/js: container reader (third implementation)
converters/python/bmo_ooxml: docx/xlsx/pptx/csv/md/pdf importers+exporters
calc/python/bmo_calc: formula parser + evaluator (Excel semantics)
renderer/python/bmo_render: SVG/HTML reference rendering (RFC-0010)
core: tests/conformance/bmo_reader.py  (executable reference: parse/build/validate/commit)
      core/rust/                        (mirror reference core; needs toolchain)
spec/: RFC-0001..0010 (authoritative; code loses arguments to spec)
```

## Key design rules (enforced, not advised)

1. **Bytes are never rewritten in place.** Edits append chunks + directory
   + root + footer (txn+1). `append_commit` + `full_check`-before-write.
2. **Preservation is architectural.** Unknown ancillary chunks/kinds/keys
   survive because payloads are re-indexed, never re-serialized.
3. **Validation is layered:** container (magic/CRC/indexes) → structure
   (R1–R6) → domains (vocabularies/addresses/membership). Each layer has
   its own error codes; diagnostics are part of the contract.
4. **No execution on open.** Parsers never run embedded content; crypto
   uses audited libs (`cryptography`, reference KDF/signature harnesses
   only in tests); pure-Python crypto exists SOLELY for vectors.
5. **Determinism:** fixed IDs/timestamps in builders, canonical CBOR,
   sorted directories, pinned goldens (byte-identical rebuilds).

## Data flow: OOXML -> BMO -> everything

`import_*` produce envelope dicts (+ assets) → `container.genesis`
frames them → readers index via footer→root→directory → domains validate
→ renderer/calc/MCP/CLI consume. Exporters walk the same envelopes back
to OOXML. Fidelity is measured per category (`compat_matrix.json`),
never asserted in prose.

## Third-party boundaries (adopted, vendored nowhere)

`cryptography` (AES-GCM / ChaCha20-Poly1305), `zstandard`, `python-docx`/
`openpyxl`/`python-pptx` (oracle verification + differential testing),
`pdfplumber`+`pypdf` (PDF import), `pymupdf` (test-PDF builder only),
`mcp` SDK (client-side interop tests only), PyInstaller (exe builds).
Rust core: optional `codecs` feature pulls RustCrypto `aes-gcm` +
`chacha20poly1305` + `ruzstd` (pure Rust, no C toolchain, no host build
scripts); the default build has zero required dependencies. PyMuPDF is
AGPL: import-guarded, optional, never a hard dependency.
