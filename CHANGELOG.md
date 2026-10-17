# Changelog

All notable changes to BMO will be documented here.

## Python toolkit 0.1.0a1 — 2026-10-18

This is the current 0.x alpha development version. The BMO format has no
stable release; the JavaScript SDK is distributed separately as an alpha
npm package. Changes are tracked here and in the project roadmap.

### Added

- Nested DOCX tables are preserved through import, model validation, and
  export; the complex DOCX fixture retains 35/35 model elements.
- DOCX named-style inheritance, run/paragraph formatting, paragraph spacing
  and indents, and multilevel list formats/markers/restarts are preserved;
  the measured `docx-style-depth` round-trip retains 11/11 model elements.
- XLSX pictures preserve two-cell, one-cell, and absolute anchors plus asset
  bytes through import/export; `xlsx-images` measures 5/5 model elements and
  3/3 assets, with Python/Rust schema validation and openpyxl interoperability.
- PDF text extraction now delegates glyph grouping to pdfplumber after
  excluding detected table regions, preserving coherent text on rotated pages.
  Upstream OpenOffice and PDF.js fixtures retain 11/11 expected text lines;
  source and checksums are recorded alongside the fixtures.
- TypeScript declarations now match the JavaScript SDK's runtime export
  shapes and include the `info` and `fail` APIs.
- PPTX embedded audio/video is preserved as SHA-256-addressed assets; the
  deterministic round-trip fixture retains 2/2 media assets.

### Fixed and verified

- PPTX export and fixture code parse on Python 3.11; the clean-checkout
  GitHub Actions matrix passes current-source Python, Rust, interop, SDK,
  and Windows app checks.
- Converter tests merge their measurements into `compat_matrix.json`
  instead of erasing real-world corpus results.
- The CBOR/model fuzz target is part of the portable suite; it exposed and
  fixed an uncaught `TypeError` for unhashable CBOR map keys.
- Updated vulnerable Python dependency pins for `anyio`, `cryptography`,
  `PyNaCl`, and `pypdf`; CI now audits the Python and Rust lockfiles and
  publishes a CycloneDX SBOM for Python dependencies.
- Updated the locked CFFI version to satisfy `cryptography` 50's CPython
  requirement. Rust dependency auditing narrowly excludes the unpatched
  RSA private-key timing advisory because the optional Rust implementation
  performs public-key signature verification only.
- Packaged the Python CLI and toolkit as `bmo-format` for Python 3.11+;
  PDF import and optional cryptographic/compression codecs are installable
  as extras.

## JavaScript SDK 0.1.0-alpha.0 — 2026-10-17

- Published `bmo-format-sdk@0.1.0-alpha.0` on npm under the `alpha` tag.
- Includes the zero-dependency container reader and TypeScript declarations.
- This is an SDK prerelease, not a stable BMO format release.
