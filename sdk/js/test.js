/** JS SDK conformance (mirrors the Python/Rust container suites). Run: node --test sdk/js/test.js */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const bmo = require('./bmo.js');

const GOLDEN = path.join(__dirname, '..', '..', 'tests', 'golden');

test('hello opens, 829 bytes, 3 objects', () => {
  const raw = fs.readFileSync(path.join(GOLDEN, 'hello.bmo'));
  assert.equal(raw.length, 829);
  const f = bmo.openFile(raw);
  assert.equal(f.entries.map((e) => e.oid).join(','), '1,2,3');
  const rep = bmo.validate(f);
  assert.equal(rep.objects, 3);
  assert.equal(rep.pages, 1);
  assert.equal(rep.format, '0.1');
});

test('crc32c vector', () => {
  assert.equal(bmo.crc32c(Buffer.from('123456789')), 0xe3069283);
  assert.equal(bmo.crc32c(Buffer.alloc(0)), 0);
});

test('tampered payload rejected', () => {
  const raw = Buffer.from(fs.readFileSync(path.join(GOLDEN, 'hello.bmo')));
  raw[489] ^= 0xff;
  assert.throws(() => bmo.openFile(raw), (e) => ['PAYLOAD_CRC_MISMATCH', 'CORRUPT'].includes(e.code));
});

test('bad magic rejected', () => {
  const raw = Buffer.from(fs.readFileSync(path.join(GOLDEN, 'hello.bmo')));
  raw[0] = 0x58;
  assert.throws(() => bmo.openFile(raw), { code: 'BAD_MAGIC' });
});

test('model/text/sheet/deck/table open', () => {
  for (const n of ['model.bmo', 'text.bmo', 'sheet.bmo', 'deck.bmo', 'table.bmo', 'graphics.bmo']) {
    const f = bmo.openFile(fs.readFileSync(path.join(GOLDEN, n)));
    assert.ok(bmo.validate(f).objects > 0, n);
  }
});

test('float cells decode (sheet C3 ~= 3.14)', () => {
  const f = bmo.openFile(fs.readFileSync(path.join(GOLDEN, 'sheet.bmo')));
  const entry = f.entries.find((e) => e.oid === 3);
  const [v] = bmo.cborDecode(bmo.readObject(f, entry.oid));
  assert.ok(Math.abs(v.body.cells.C3.v - 3.14) < 1e-9);
});
