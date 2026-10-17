/**
 * BMO JavaScript SDK — TypeScript declarations  (Issue #13)
 * Mirrors the runtime exports of bmo.js (zero-dependency container reader).
 * All bit-widths and field names match RFC-0001/RFC-0002.
 */

// ─── Low-level types ────────────────────────────────────────────────────────

/** A 32-bit CRC32C checksum (unsigned). */
export type Crc32c = number;

/** Chunk content-type code (e.g. 0x0010 = generic object chunk). */
export type ChunkType = number;

// ─── Header ─────────────────────────────────────────────────────────────────

export interface BmoHeader {
    /** Major format version (currently 0). */
    major: number;
    /** Minor format version (currently 1). */
    minor: number;
    /** Header flags bitmask. */
    flags: number;
    /** 16-byte file identifier. */
    fileId: Buffer;
    /** Creation time in milliseconds since the Unix epoch. */
    createdMs: bigint;
}

// ─── Directory entry ─────────────────────────────────────────────────────────

export interface BmoDirEntry {
    /** Object identifier (1-indexed within this container). */
    oid: number;
    /** Content-type code (0x0010 for envelope objects, 0x0005 for data chunks). */
    type: ChunkType;
    /** Byte offset of the object chunk from the start of the file. */
    offset: number;
    /** Total length of the chunk, including header and payload checksum. */
    total: number;
}

// ─── Footer ─────────────────────────────────────────────────────────────────

export interface BmoFooter {
    /** Byte offset of the root chunk from start of file. */
    rootOffset: number;
    /** Length of the root chunk. */
    rootLen: number;
    /** Transaction counter (incremented on each append). */
    txn: number;
    /** Previous footer offset, or zero for the first transaction. */
    prev: number;
}

// ─── Opened file ─────────────────────────────────────────────────────────────

/**
 * Return value of {@link openFile}. Holds the parsed metadata and the raw
 * buffer so other functions can read object payloads on demand.
 */
export interface BmoFile {
    /** Parsed file header. */
    header: BmoHeader;
    /** Parsed file footer. */
    footer: BmoFooter;
    /** Byte offset of the selected footer. */
    footerOffset: number;
    /** Directory entries, one per logical object. */
    entries: BmoDirEntry[];
    /** The original file bytes used for validated random-access reads. */
    raw: Buffer;
}

// ─── Validation report ───────────────────────────────────────────────────────

export interface BmoValidateReport {
    /** Total number of directory entries (objects). */
    objects: number;
    /** Number of decoded objects whose kind is `page`. */
    pages: number;
    /** Format version string e.g. `"0.1"`. */
    format: string;
    /** True when the container header marks it as encrypted. */
    encrypted: boolean;
    /** Current transaction number. */
    txn: number;
}

/** Summary returned by `info` and `validate`. */
export type BmoInfo = BmoValidateReport;

// ─── Decoded CBOR ────────────────────────────────────────────────────────────

/**
 * Decoded CBOR value. BMO envelopes use a small subset of CBOR (RFC 7049):
 * text strings, byte strings, unsigned ints, booleans, floats, arrays, maps.
 * Byte strings are returned as `Buffer`; maps become `Record<string, CborValue>`.
 */
export type CborValue =
    | null
    | boolean
    | number
    | string
    | Buffer
    | CborValue[]
    | { [key: string]: CborValue };

// ─── Errors ──────────────────────────────────────────────────────────────────

/**
 * All errors thrown by the BMO SDK. The `code` property carries a
 * machine-readable error key (never changes between releases).
 */
export interface BmoError extends Error {
    /**
     * Machine-readable error codes:
     * - `BAD_MAGIC`          – first 4 bytes are not the BMO magic
     * - `CORRUPT`            – structural invariant violated
     * - `ROOT_MISMATCH`      – root chunk type is not ROOT (0x03)
     * - `PAYLOAD_CRC_MISMATCH` – CRC32C of a chunk payload failed
     * - `NEED_KEY`           – encrypted payload, no decryption key provided
     * - `TRUNCATED`          – file ends before expected offset
     */
    code: BmoErrorCode;
}

/** Stable machine-readable codes emitted by the JavaScript reader. */
export type BmoErrorCode =
    | "TOO_SMALL"
    | "BAD_MAGIC"
    | "BAD_HEADER_LENGTH"
    | "INCOMPATIBLE_MAJOR"
    | "UPGRADE_REQUIRED"
    | "HEADER_CRC_MISMATCH"
    | "TRUNCATED"
    | "CHUNK_HEADER_CRC_MISMATCH"
    | "CORRUPT"
    | "LIMIT_EXCEEDED"
    | "UNKNOWN_CRITICAL"
    | "PAYLOAD_CRC_MISMATCH"
    | "NO_VALID_FOOTER"
    | "FOOTER_CRC_MISMATCH"
    | "TXN_MISMATCH"
    | "ROOT_MISMATCH"
    | "DUPLICATE_OBJECT_ID"
    | "DIRECTORY_UNSORTED";

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Compute the CRC32C (Castagnoli) checksum of `data`.
 *
 * @param data  - Input bytes (may be empty → returns 0).
 * @returns       32-bit unsigned CRC32C value.
 *
 * @example
 * ```ts
 * import * as bmo from "./bmo.js";
 * bmo.crc32c(Buffer.from("123456789")); // 0xe3069283
 * ```
 */
export declare function crc32c(data: Buffer): Crc32c;

/**
 * Open and fully parse a BMO container.
 *
 * Validates the container framing, footer/root/directory, and chunk CRCs.
 * Throws a {@link BmoError} on any violation.
 *
 * @param buf - Raw file bytes.
 * @returns     Parsed {@link BmoFile} ready for further inspection.
 *
 * @throws {@link BmoError} with code `BAD_MAGIC` / `CORRUPT` / `PAYLOAD_CRC_MISMATCH`
 *
 * @example
 * ```ts
 * import { openFile } from "./bmo.js";
 * import { readFileSync } from "node:fs";
 * const f = openFile(readFileSync("hello.bmo"));
 * console.log(f.entries.length); // 3
 * ```
 */
export declare function openFile(buf: Buffer): BmoFile;

/**
 * Validate a parsed BMO file and return high-level statistics.
 *
 * Rechecks framing and checksums and returns the reader's summary.
 *
 * @param file - Result of {@link openFile}.
 * @returns      {@link BmoValidateReport} with object counts and format version.
 */
export declare function validate(file: BmoFile): BmoValidateReport;

/**
 * Read the decoded payload of one object. This reader rejects unsupported
 * codecs rather than decompressing or decrypting them.
 *
 * @param file - Parsed container.
 * @param oid  - Object identifier (1-indexed; must appear in `file.entries`).
 * @returns      Raw CBOR bytes for the object payload.
 *
 * @throws {@link BmoError} with code `CORRUPT` if the OID is not found.
 */
export declare function readObject(file: BmoFile, oid: number): Buffer;

/**
 * Decode a CBOR-encoded byte buffer (RFC 7049 — subset used by BMO).
 *
 * Returns a tuple `[value, bytesConsumed]`. Call with the full payload of
 * an object returned by {@link readObject} to decode the envelope body.
 *
 * Supports: integers, text strings, byte strings, arrays, maps, booleans,
 * null, and 16/32/64-bit floats. Numeric values use JavaScript `number`.
 * Indefinite-length items and tags are not supported.
 *
 * @param buf - CBOR-encoded bytes.
 * @returns     `[decoded, bytesConsumed]`
 *
 * @throws {@link BmoError} with code `CORRUPT` for malformed CBOR.
 *
 * @example
 * ```ts
 * const [env] = bmo.cborDecode(bmo.readObject(f, 1));
 * console.log(env.kind); // "document"
 * ```
 */
export declare function cborDecode(buf: Buffer, pos?: number): [CborValue, number];

/** Return a summary of an opened file without revalidating its chunks. */
export declare function info(file: BmoFile): BmoInfo;

/** Throw an error carrying the supplied machine-readable code. */
export declare function fail(code: BmoErrorCode, extra?: string): never;
