# Changelog

All notable changes to BMO will be documented here.

## Unreleased

This is the current 0.x alpha development version. No versioned public
release has been published. Changes are tracked in the project history
and roadmap; a release entry will be added when a release is authorized
and its contents have been verified.

### Added

- Nested DOCX tables are preserved through import, model validation, and
  export; the complex DOCX fixture retains 35/35 model elements.
- TypeScript declarations now match the JavaScript SDK's runtime export
  shapes and include the `info` and `fail` APIs.
- PPTX embedded audio/video is preserved as SHA-256-addressed assets; the
  deterministic round-trip fixture retains 2/2 media assets.
- The JavaScript SDK is packaged for npm as `bmo-format-sdk`.
