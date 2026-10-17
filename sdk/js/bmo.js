/** BMO container reader for JavaScript (mirrors bmo-core container layer).
 * Zero dependencies. Scope: open / random-access read / validate framing.
 * Envelope R-rules live in host apps (or the Rust core); this SDK reads
 * envelopes for navigation (kind/parent) without enforcing domain rules.
 */
'use strict';

const POLY = 0x82f63b78;
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (c >>> 1) ^ POLY : c >>> 1;
    t[i] = c >>> 0;
  }
  return t;
})();

function crc32c(buf, start = 0, end = buf.length) {
  let c = 0xffffffff;
  for (let i = start; i < end; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function fail(code, extra = '') {
  const e = new Error(extra ? `${code}: ${extra}` : code);
  e.code = code;
  throw e;
}

const HEADER_LEN = 64, CHUNK_HDR = 40, FOOTER_LEN = 64;
const KNOWN = new Set([1, 2, 3, 4, 5, 6, 7, 8, 0x10]);

function parseHeader(buf) {
  if (buf.length < 128) fail('TOO_SMALL');
  if (!(buf[0] === 0x42 && buf[1] === 0x4d && buf[2] === 0x4f && buf[3] === 0x1a)) fail('BAD_MAGIC');
  const major = buf.readUInt16LE(4), minor = buf.readUInt16LE(6);
  if (buf.readUInt16LE(8) !== 64 || buf.readUInt16LE(10) !== 1) fail('BAD_HEADER_LENGTH');
  if (major !== 0) fail('INCOMPATIBLE_MAJOR');
  if (minor !== 1) fail('UPGRADE_REQUIRED');
  const stored = buf.readUInt32LE(32);
  const cat = Buffer.concat([buf.subarray(0, 32), buf.subarray(36, 64)]);
  if (crc32c(cat) !== stored) fail('HEADER_CRC_MISMATCH');
  return { major, minor, flags: buf.readUInt32LE(12), fileId: buf.subarray(16, 32), createdMs: buf.readBigInt64LE(36) };
}

function parseChunkHeader(buf, off) {
  if (off + CHUNK_HDR > buf.length) fail('TRUNCATED');
  const h = {
    type: buf.readUInt32LE(off), version: buf.readUInt16LE(off + 4),
    flags: buf.readUInt16LE(off + 6),
    oid: Number(buf.readBigUInt64LE(off + 8)),
    storedLen: Number(buf.readBigUInt64LE(off + 16)),
    decodedLen: Number(buf.readBigUInt64LE(off + 24)),
    codec: buf.readUInt32LE(off + 32),
  };
  if (crc32c(buf, off, off + 36) !== buf.readUInt32LE(off + 36)) fail('CHUNK_HEADER_CRC_MISMATCH');
  if (h.type === 0) fail('CORRUPT');
  if (h.storedLen > 0xffffffff || h.decodedLen > 0xffffffff) fail('LIMIT_EXCEEDED');
  const comp = h.codec & 0xffff, enc = (h.codec >>> 16) & 0xffff;
  if (!!(h.flags & 1) !== !!comp || !!(h.flags & 2) !== !!enc) fail('CORRUPT');
  if (comp !== 0 || enc !== 0) fail('UPGRADE_REQUIRED');
  if ((h.flags & 0xfff0) && (h.flags & 8)) fail('UNKNOWN_CRITICAL');
  h.critical = !!(h.flags & 8);
  h.totalLen = CHUNK_HDR + h.storedLen + 4;
  return h;
}

function readChunk(buf, off, end) {
  const h = parseChunkHeader(buf, off);
  if (off + h.totalLen > end) fail('TRUNCATED');
  const payload = buf.subarray(off + CHUNK_HDR, off + CHUNK_HDR + h.storedLen);
  if (crc32c(payload) !== buf.readUInt32LE(off + CHUNK_HDR + h.storedLen)) fail('PAYLOAD_CRC_MISMATCH');
  if (h.storedLen !== h.decodedLen) fail('CORRUPT');
  return { header: h, payload };
}

function parseFooter(buf) {
  if (buf.length !== FOOTER_LEN) fail('NO_VALID_FOOTER');
  if (!(buf[0] === 0x42 && buf[1] === 0x4d && buf[2] === 0x4f && buf[3] === 0x46)) fail('NO_VALID_FOOTER');
  if (!(buf[60] === 0x42 && buf[61] === 0x4d && buf[62] === 0x4f && buf[63] === 0x46)) fail('NO_VALID_FOOTER');
  if (buf.readUInt16LE(4) !== 1) fail('UPGRADE_REQUIRED');
  if (crc32c(buf, 0, 40) !== buf.readUInt32LE(40)) fail('FOOTER_CRC_MISMATCH');
  const txn = Number(buf.readBigUInt64LE(24));
  if (txn === 0) fail('CORRUPT');
  return {
    rootOffset: Number(buf.readBigUInt64LE(8)), rootLen: Number(buf.readBigUInt64LE(16)),
    txn, prev: Number(buf.readBigUInt64LE(32)),
  };
}

function findFooter(buf) {
  if (buf.length < 128) fail('TOO_SMALL');
  try {
    return { offset: buf.length - FOOTER_LEN, footer: parseFooter(buf.subarray(buf.length - FOOTER_LEN)) };
  } catch { /* torn tail: scan back */ }
  const lo = Math.max(0, buf.length - 16 * 1024 * 1024 - FOOTER_LEN);
  for (let off = buf.length - FOOTER_LEN - 1; off >= lo; off--) {
    if (buf[off] === 0x42 && buf[off + 60] === 0x46) {
      try {
        return { offset: off, footer: parseFooter(buf.subarray(off, off + FOOTER_LEN)) };
      } catch { /* keep scanning */ }
    }
  }
  fail('NO_VALID_FOOTER');
}

// --- minimal CBOR decode (envelope navigation only) ---
function cborArg(buf, pos, ai) {
  if (ai < 24) return [ai, pos];
  if (ai === 24) return [buf[pos], pos + 1];
  if (ai === 25) return [buf.readUInt16BE(pos), pos + 2];
  if (ai === 26) return [buf.readUInt32BE(pos), pos + 4];
  if (ai === 27) return [Number(buf.readBigUInt64BE(pos)), pos + 8];
  fail('CORRUPT');
}
function cborDecode(buf, pos = 0) {
  const ib = buf[pos++];
  const major = ib >> 5, ai = ib & 31;
  if (major === 7) {
    if (ai === 20) return [false, pos];
    if (ai === 21) return [true, pos];
    if (ai === 22) return [null, pos];
    if (ai === 25) {
      const bits = buf.readUInt16BE(pos);
      const s = (bits >> 15) & 1, e = (bits >> 10) & 31, m = bits & 1023;
      let v;
      if (e === 0) v = m * 2 ** -24;
      else if (e === 31) v = m ? NaN : Infinity;
      else v = (1 + m / 1024) * 2 ** (e - 15);
      return [s ? -v : v, pos + 2];
    }
    if (ai === 26) return [buf.readFloatBE(pos), pos + 4];
    if (ai === 27) return [buf.readDoubleBE(pos), pos + 8];
    fail('CORRUPT');
  }
  const [arg, p2] = cborArg(buf, pos, ai);
  pos = p2;
  if (major === 0) return [arg, pos];
  if (major === 1) return [-1 - arg, pos];
  if (major === 2) return [buf.subarray(pos, pos + arg), pos + arg];
  if (major === 3) return [buf.toString('utf8', pos, pos + arg), pos + arg];
  if (major === 4) {
    const out = [];
    for (let i = 0; i < arg; i++) { const [v, p] = cborDecode(buf, pos); pos = p; out.push(v); }
    return [out, pos];
  }
  if (major === 5) {
    const out = {};
    for (let i = 0; i < arg; i++) {
      const [k, p1] = cborDecode(buf, pos); pos = p1;
      const [v, p2b] = cborDecode(buf, pos); pos = p2b;
      out[typeof k === 'string' ? k : JSON.stringify(k)] = v;
    }
    return [out, pos];
  }
  fail('CORRUPT');
}

function openFile(buf) {
  const header = parseHeader(buf);
  const { offset: foff, footer } = findFooter(buf);
  const end = buf.length;
  const root = readChunk(buf, footer.rootOffset, end);
  if (root.header.type !== 3) fail('ROOT_MISMATCH');
  const m = root.payload.readUInt32LE(4);
  if (root.payload.readUInt32LE(0) !== 1 || root.payload.length !== 24 + m * 24) fail('CORRUPT');
  const dirOff = Number(root.payload.readBigUInt64LE(8));
  const dir = readChunk(buf, dirOff, end);
  if (dir.header.type !== 2) fail('CORRUPT');
  const count = dir.payload.readUInt32LE(4);
  if (dir.payload.readUInt32LE(0) !== 1 || dir.payload.length !== 16 + count * 32) fail('CORRUPT');
  const txn = Number(dir.payload.readBigUInt64LE(8));
  if (txn !== footer.txn) fail('TXN_MISMATCH');
  const entries = [];
  let prev = -1;
  for (let i = 0; i < count; i++) {
    const o = 16 + i * 32;
    const oid = Number(dir.payload.readBigUInt64LE(o));
    if (oid <= prev) fail(oid === prev ? 'DUPLICATE_OBJECT_ID' : 'DIRECTORY_UNSORTED');
    prev = oid;
    entries.push({ oid, type: dir.payload.readUInt32LE(o + 8), offset: Number(dir.payload.readBigUInt64LE(o + 16)), total: Number(dir.payload.readBigUInt64LE(o + 24)) });
  }
  for (const e of entries) {
    const c = readChunk(buf, e.offset, end);
    if (c.header.oid !== e.oid || c.header.type !== e.type || c.header.totalLen !== e.total) fail('CORRUPT');
    if (!KNOWN.has(c.header.type) && c.header.critical) fail('UNKNOWN_CRITICAL');
  }
  return { header, footer, footerOffset: foff, entries, raw: buf };
}

function readObject(file, oid) {
  const e = file.entries.find((x) => x.oid === oid);
  if (!e) fail('CORRUPT');
  return readChunk(file.raw, e.offset, file.raw.length).payload;
}

function validate(file) {
  // full region walk (footers skipped), then info
  const seen = new Set();
  let off = HEADER_LEN;
  const end = file.raw.length;
  // collect footer offsets by walking history is overkill here: skip any valid footer found
  while (off < end) {
    if (off + FOOTER_LEN <= end) {
      try { parseFooter(file.raw.subarray(off, off + FOOTER_LEN)); off += FOOTER_LEN; continue; } catch { /* not a footer */ }
    }
    if (off + CHUNK_HDR > end) fail('TRUNCATED');
    const h = parseChunkHeader(file.raw, off);
    if (off + h.totalLen > end) fail('TRUNCATED');
    readChunk(file.raw, off, end);
    if (!KNOWN.has(h.type) && h.critical) fail('UNKNOWN_CRITICAL');
    if (seen.has(off)) fail('CORRUPT');
    seen.add(off);
    off += h.totalLen;
  }
  return info(file);
}

function info(file) {
  let pages = 0;
  for (const e of file.entries) {
    if (e.type !== 0x10) continue;
    try {
      const [v] = cborDecode(readObject(file, e.oid));
      if (v && v.kind === 'page') pages++;
    } catch { /* navigation best-effort */ }
  }
  return {
    format: `${file.header.major}.${file.header.minor}`, objects: file.entries.length,
    pages, encrypted: !!(file.header.flags & 1), txn: file.footer.txn,
  };
}

module.exports = { crc32c, openFile, readObject, validate, info, cborDecode, fail };
