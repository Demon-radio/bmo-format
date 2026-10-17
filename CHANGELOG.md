# Changelog

All notable changes to BMO will be documented here.

## Unreleased

This is the current 0.x alpha development version. The BMO format has no
stable release; the JavaScript SDK is distributed separately as an alpha
npm package. Changes are tracked here and in the project roadmap.

### Added

- Nested DOCX tables are preserved through import, model validation, and
  export; the complex DOCX fixture retains 35/35 model elements.
- TypeScript declarations now match the JavaScript SDK's runtime export
  shapes and include the `info` and `fail` APIs.
- PPTX embedded audio/video is preserved as SHA-256-addressed assets; the
  deterministic round-trip fixture retains 2/2 media assets.
- The JavaScript SDK is packaged for npm as `bmo-format-sdk`.
