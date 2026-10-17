# BMO JavaScript SDK

Zero-dependency CommonJS reader for BMO (`.bmo`) binary containers. It
validates container framing and checksums, reads object payloads by ID, and
decodes the CBOR subset used by BMO. TypeScript declarations are included.

This is an alpha container reader, not a complete document-model validator.
It does not decrypt or decompress optional codecs; those inputs fail closed
with a stable error code.

## Install

```sh
npm install bmo-format-sdk
```

Requires Node.js 18 or newer.

## Example

```js
const { readFileSync } = require("node:fs");
const { openFile, info, readObject, cborDecode } = require("bmo-format-sdk");

const file = openFile(readFileSync("example.bmo"));
console.log(info(file));
const [envelope] = cborDecode(readObject(file, file.entries[0].oid));
console.log(envelope.kind);
```

See the project `spec/` directory for the format RFCs and `bmo.d.ts` for the
complete API types. Code is licensed under Apache-2.0; see `LICENSE`.
