//! File open / validate / build / append — RFC-0001 sections 7, 8, 11, 12.
//!
//! Reference semantics: atomic footer commit, copy-on-write, unknown
//! ancillary preservation. This build holds the file in memory for
//! clarity; a production engine swaps the byte slice for mmap/Seek
//! without changing any offsets or checks (all positions are u64).

use std::collections::{HashMap, HashSet};

use crate::cbor::{self, Value};
use crate::chunk::{
    self, Chunk, ChunkHeader, FLAG_CRITICAL, FLAG_HAS_OBJECT_ID, T_ASSET_TABLE,
    T_DOCUMENT, T_GENERIC_OBJECT, T_INDEX_SECONDARY, T_OBJECT_DIRECTORY,
    T_ROOT_INDEX, CHUNK_HEADER_LEN,
};
use crate::directory::{DirEntry, ObjectDirectory};
use crate::error::{BmoError, BmoResult};
use crate::footer::{self, Footer, FOOTER_LEN};
use crate::header::{FileHeader, HEADER_LEN};
use crate::root::{RootIndex, SecondaryRef};

pub const BUILD_MAJOR: u16 = 0;
pub const BUILD_MINOR: u16 = 1;

/// Validated open file.
#[derive(Debug, Clone)]
pub struct BmoFile {
    pub header: FileHeader,
    pub footer: Footer,
    pub footer_offset: u64,
    pub root: RootIndex,
    pub dir: ObjectDirectory,
    /// Raw file bytes (reference build keeps them for random access).
    pub raw: Vec<u8>,
    /// DOCUMENT payload (decoded) for version/extension gates.
    pub document: Vec<u8>,
    /// All footer offsets in history order (oldest..newest).
    pub history: Vec<u64>,
    /// Content key for encrypted chunks (AES-256-GCM enc=1 / ChaCha20-
    /// Poly1305 enc=2, from the SECURITY chunk in a full implementation).
    /// `None` = keyless open: encrypted entries fail closed with NEED_KEY
    /// when touched.
    pub content_key: Option<[u8; 32]>,
}

impl BmoFile {
    /// Open + fully validate the index chain (not every payload's model
    /// semantics — those are RFC-0002+). Payload CRCs ARE verified on
    /// every chunk touched through the directory + DOCUMENT.
    pub fn open_bytes(raw: Vec<u8>) -> BmoResult<Self> {
        Self::open_bytes_with_key(raw, None)
    }

    /// Keyed open: `content_key` unlocks encrypted chunks (AES-256-GCM
    /// enc=1 / ChaCha20-Poly1305 enc=2, codecs feature). Without it,
    /// encrypted entries fail with NEED_KEY at the same points the Python
    /// kit does (fail closed).
    pub fn open_bytes_with_key(raw: Vec<u8>, content_key: Option<[u8; 32]>) -> BmoResult<Self> {
        if raw.len() < HEADER_LEN + FOOTER_LEN + 1 {
            return Err(BmoError::TooSmall);
        }
        let header = FileHeader::parse(&raw)?;
        let (footer_offset, footer) = footer::find_footer(&raw)?;

        // Bounds check root pointer before touching.
        let rend = footer.root_offset.checked_add(footer.root_len).ok_or(BmoError::Corrupt)?;
        if rend > raw.len() as u64 || footer.root_offset < HEADER_LEN as u64 {
            return Err(BmoError::RootMismatch);
        }
        let root_chunk = Chunk::read_at_with_key(&raw, footer.root_offset, raw.len() as u64, content_key.as_ref())?;
        if root_chunk.header.chunk_type != T_ROOT_INDEX {
            return Err(BmoError::RootMismatch);
        }
        let root = RootIndex::parse(&root_chunk.payload)?;

        let dend = root.dir_offset.checked_add(root.dir_len).ok_or(BmoError::Corrupt)?;
        if dend > raw.len() as u64 {
            return Err(BmoError::Corrupt);
        }
        let dir_chunk = Chunk::read_at_with_key(&raw, root.dir_offset, raw.len() as u64, content_key.as_ref())?;
        if dir_chunk.header.chunk_type != T_OBJECT_DIRECTORY {
            return Err(BmoError::Corrupt);
        }
        let dir = ObjectDirectory::parse(&dir_chunk.payload)?;
        if dir.txn_id != footer.txn_id {
            return Err(BmoError::TxnMismatch);
        }

        // Verify every directory entry resolves to a live chunk.
        for e in &dir.entries {
            if e.file_offset == crate::directory::TOMBSTONE {
                continue;
            }
            let c = Chunk::read_at_with_key(&raw, e.file_offset, raw.len() as u64, content_key.as_ref())?;
            if c.header.object_id != e.object_id || c.header.chunk_type != e.chunk_type {
                return Err(BmoError::Corrupt);
            }
            let total = c.header.total_len()?;
            if total != e.total_len {
                return Err(BmoError::Corrupt);
            }
            // Unknown critical objects fail closed; ancillary pass through.
            if !chunk::is_known_type(c.header.chunk_type) && c.header.is_critical() {
                return Err(BmoError::UnknownCritical);
            }
        }

        // DOCUMENT gate: must exist exactly once at file level; versions
        // and docId must agree with the header.
        let document = load_document(&raw, &header, content_key.as_ref())?;
        gate_document(&document, &header)?;

        // History chain for archival/rollback (best effort; broken links
        // beyond the tip do not fail the open — tip is authoritative).
        let history = collect_history(&raw, footer_offset, footer.prev_footer_offset);

        Ok(Self { header, footer, footer_offset, root, dir, raw, document, history, content_key })
    }

    /// Random-access read of one object payload (decompressed, decrypted).
    pub fn read_object(&self, object_id: u64) -> BmoResult<Vec<u8>> {
        let e = self.dir.find(object_id).ok_or(BmoError::Corrupt)?;
        if e.file_offset == crate::directory::TOMBSTONE {
            return Err(BmoError::Corrupt);
        }
        Ok(Chunk::read_at_with_key(&self.raw, e.file_offset, self.raw.len() as u64, self.content_key.as_ref())?.payload)
    }

    pub fn validate(&self) -> BmoResult<FileInfo> {
        // Index chain already verified in open; validate() additionally
        // walks every chunk region (including history) for CRC soundness,
        // rejects unknown-critical bytes, collects asset tables, then
        // enforces RFC-0002 structural rules R1–R6 and the RFC-0003..0006
        // domain body rules.
        let tables = walk_all_chunks(&self.raw, &self.history, self.content_key.as_ref())?;
        let load = |id: u64| self.read_object(id);
        crate::model::validate_structure(&self.dir.entries, &load, &tables)?;
        crate::domains::validate_bodies(&self.dir.entries, &load)?;
        Ok(self.info())
    }

    pub fn info(&self) -> FileInfo {
        let mut pages = 0u32;
        let mut assets = 0u32;
        for e in &self.dir.entries {
            if e.chunk_type == crate::chunk::T_ASSET_DATA {
                assets += 1;
            }
            if e.chunk_type == T_GENERIC_OBJECT {
                if let Ok(c) = Chunk::read_at_with_key(&self.raw, e.file_offset, self.raw.len() as u64, self.content_key.as_ref()) {
                    if let Ok((Value::Map(pairs), _)) = cbor::decode(&c.payload) {
                        if let Some(Value::Text(k)) = cbor::map_get(&pairs, "kind") {
                            if k == "page" {
                                pages += 1;
                            }
                        }
                    }
                }
            }
        }
        let objects = self.dir.entries.iter().filter(|e| e.file_offset != crate::directory::TOMBSTONE).count() as u32;
        FileInfo {
            major: self.header.major,
            minor: self.header.minor,
            objects,
            pages,
            assets,
            encrypted: self.header.flags & crate::header::FLAG_ENCRYPTED != 0,
            signed: self.header.flags & crate::header::FLAG_SIGNED != 0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileInfo {
    pub major: u16,
    pub minor: u16,
    pub objects: u32,
    pub pages: u32,
    pub assets: u32,
    pub encrypted: bool,
    pub signed: bool,
}

impl FileInfo {
    /// Exact CLI surface expected by RFC-0001 section 16.
    pub fn report(&self) -> String {
        format!(
            "BMO Format: {}.{}\nContainer: Binary\nObjects: {}\nPages: {}\nAssets: {}\nCompression: NONE\nIntegrity: CRC32C+SHA-256(manifest)\nEncrypted: {}\nSigned: {}",
            self.major,
            self.minor,
            self.objects,
            self.pages,
            self.assets,
            if self.encrypted { "Yes" } else { "No" },
            if self.signed { "Yes" } else { "No" },
        )
    }
}

fn load_document(raw: &[u8], header: &FileHeader, content_key: Option<&[u8; 32]>) -> BmoResult<Vec<u8>> {
    // DOCUMENT has object_id 0 so it is not in the directory; scan the
    // first transaction region for it (writers place it first).
    let mut off = HEADER_LEN as u64;
    let end = raw.len() as u64;
    let mut found: Option<Vec<u8>> = None;
    while off + CHUNK_HEADER_LEN as u64 <= end {
        // Stop at any valid footer (history boundary).
        if off + FOOTER_LEN as u64 <= end {
            if let Ok(_) = Footer::parse(&raw[off as usize..off as usize + FOOTER_LEN]) {
                // Jump past footer; keep scanning later regions too so a
                // compacted file (DOCUMENT rewritten at new offset) works.
                off += FOOTER_LEN as u64;
                continue;
            }
        }
        let h = match ChunkHeader::parse(&raw[off as usize..]) {
            Ok(h) => h,
            // Future codecs / unknown critical must fail closed, never be
            // mistaken for end-of-scan. Framing errors end the scan.
            Err(e @ (BmoError::UnknownCritical | BmoError::UpgradeRequired)) => return Err(e),
            Err(_) => break,
        };
        let total = h.total_len()?;
        if off + total > end {
            break;
        }
        if h.chunk_type == T_DOCUMENT {
            let c = Chunk::read_at_with_key(raw, off, end, content_key)?;
            if found.is_some() {
                return Err(BmoError::Corrupt); // two DOCUMENTs
            }
            // DocId cross-check needs the payload now.
            check_doc_id(&c.payload, header)?;
            found = Some(c.payload);
        } else if !chunk::is_known_type(h.chunk_type) && (h.flags & FLAG_CRITICAL) != 0 {
            return Err(BmoError::UnknownCritical);
        }
        off += total;
    }
    found.ok_or(BmoError::Corrupt)
}

fn check_doc_id(doc_payload: &[u8], header: &FileHeader) -> BmoResult<()> {
    let (v, _) = cbor::decode(doc_payload)?;
    let pairs = match v {
        Value::Map(p) => p,
        _ => return Err(BmoError::Corrupt),
    };
    match cbor::map_get(&pairs, "docId") {
        Some(Value::Bytes(id)) if id.as_slice() == header.file_id => Ok(()),
        _ => Err(BmoError::DocIdMismatch),
    }
}

fn gate_document(doc_payload: &[u8], header: &FileHeader) -> BmoResult<()> {
    let (v, _) = cbor::decode(doc_payload)?;
    let pairs = match v {
        Value::Map(p) => p,
        _ => return Err(BmoError::Corrupt),
    };
    let u = |k: &str| -> BmoResult<u64> {
        match cbor::map_get(&pairs, k) {
            Some(Value::UInt(n)) => Ok(*n),
            _ => Err(BmoError::Corrupt),
        }
    };
    let (fmaj, fmin, need_maj, need_min) = (u("bmoMajor")?, u("bmoMinor")?, u("minMajor")?, u("minMinor")?);
    if fmaj != header.major as u64 || fmin != header.minor as u64 {
        return Err(BmoError::Corrupt);
    }
    if need_maj > BUILD_MAJOR as u64 || (need_maj == BUILD_MAJOR as u64 && need_min > BUILD_MINOR as u64) {
        return Err(BmoError::UpgradeRequired);
    }
    if fmaj != BUILD_MAJOR as u64 {
        return Err(BmoError::IncompatibleMajor);
    }
    if fmin > BUILD_MINOR as u64 {
        // Minor-ahead: allowed only with no unknown critical/required ext.
        // Unknown-critical already rejected in open; extensions checked here.
    }
    // v0.1 knows no extensions: any required entry fails closed.
    if let Some(Value::Array(req)) = cbor::map_get(&pairs, "requiredExtensions") {
        if !req.is_empty() {
            return Err(BmoError::UnknownExtension);
        }
    }
    Ok(())
}

fn collect_history(raw: &[u8], tip: u64, mut prev: u64) -> Vec<u64> {
    let mut chain = vec![tip];
    let mut seen: HashSet<u64> = HashSet::from([tip]);
    while prev != 0 && prev != u64::MAX {
        if prev.checked_add(FOOTER_LEN as u64).map_or(true, |e| e > raw.len() as u64) {
            break;
        }
        if !seen.insert(prev) {
            break;
        }
        match Footer::parse(&raw[prev as usize..prev as usize + FOOTER_LEN]) {
            Ok(f) => {
                chain.push(prev);
                prev = f.prev_footer_offset;
            }
            Err(_) => break,
        }
    }
    chain.reverse();
    chain
}

/// Walk every chunk region (skipping known footers) verifying header +
/// payload CRCs and rejecting unknown-critical types. Returns decoded
/// ASSET_TABLE payloads for the model layer (RFC-0002 R6).
fn walk_all_chunks(raw: &[u8], history: &[u64], content_key: Option<&[u8; 32]>) -> BmoResult<Vec<Vec<u8>>> {
    let footers: HashSet<u64> = history.iter().cloned().collect();
    let mut tables = Vec::new();
    let mut off = HEADER_LEN as u64;
    let end = raw.len() as u64;
    // Also tolerate a torn tail: stop before the last footer and let the
    // footer scan decide (open already picked the tip).
    while off < end {
        if footers.contains(&off) {
            Footer::parse(&raw[off as usize..off as usize + FOOTER_LEN])?;
            off += FOOTER_LEN as u64;
            continue;
        }
        if off + CHUNK_HEADER_LEN as u64 > end {
            // Trailing bytes that are not a footer: torn write beyond tip.
            // Open succeeded via tip, so this is unreachable in files we
            // wrote — treat as corruption for strict validate.
            return Err(BmoError::Truncated);
        }
        // If a valid footer sits here but is not in history (e.g. torn
        // duplicate), skip it rather than misreading as chunk.
        if off + FOOTER_LEN as u64 <= end {
            if Footer::parse(&raw[off as usize..off as usize + FOOTER_LEN]).is_ok() {
                off += FOOTER_LEN as u64;
                continue;
            }
        }
        let h = ChunkHeader::parse(&raw[off as usize..]).map_err(|e| match e {
            BmoError::UpgradeRequired | BmoError::UnknownCritical => e, // from the future: clean signal
            _ => BmoError::Corrupt,
        })?;
        let total = h.total_len()?;
        if off + total > end {
            return Err(BmoError::Truncated);
        }
        let chunk = Chunk::read_at_with_key(raw, off, end, content_key)?;
        if !chunk::is_known_type(h.chunk_type) && h.is_critical() {
            return Err(BmoError::UnknownCritical);
        }
        if h.chunk_type == T_ASSET_TABLE {
            tables.push(chunk.payload);
        }
        off += total;
    }
    Ok(tables)
}

// ---------------------------------------------------------------------------
// Builder: new files + copy-on-write appends.
// ---------------------------------------------------------------------------

/// In-memory file builder. v0.x builds plain chunks only.
pub struct FileBuilder {
    header: FileHeader,
    /// (type, version, flags, object_id, payload) in write order.
    chunks: Vec<(u32, u16, u16, u64, Vec<u8>)>,
    /// Raw ancillary chunks to preserve byte-exact (header+stored+trailer).
    preserved: Vec<Vec<u8>>,
    /// (index_type, payload) secondary index chunks (ancillary, oid 0).
    indices: Vec<(u32, Vec<u8>)>,
}

impl FileBuilder {
    pub fn new(file_id: [u8; 16]) -> Self {
        Self {
            header: FileHeader { major: BUILD_MAJOR, minor: BUILD_MINOR, flags: 0, file_id, created_ms: 0 },
            chunks: Vec::new(),
            preserved: Vec::new(),
            indices: Vec::new(),
        }
    }

    pub fn document(&mut self, payload_cbor: Vec<u8>) {
        self.chunks.push((T_DOCUMENT, 1, FLAG_CRITICAL, 0, payload_cbor));
    }

    pub fn object(&mut self, object_id: u64, payload_cbor: Vec<u8>) {
        self.chunks.push((T_GENERIC_OBJECT, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, object_id, payload_cbor));
    }

    /// Queue raw bytes of an unknown ancillary chunk for preservation.
    pub fn preserve_raw(&mut self, raw_chunk: Vec<u8>) {
        self.preserved.push(raw_chunk);
    }

    /// Queue a secondary index chunk (e.g. KIND_INDEX). Ancillary by
    /// construction: indexes are advisory, the directory rules.
    pub fn secondary(&mut self, index_type: u32, payload: Vec<u8>) {
        self.indices.push((index_type, payload));
    }

    /// Assemble genesis file (txn 1).
    pub fn finish(self, doc_entries_extra: Vec<DirEntry>) -> Vec<u8> {
        let mut out = self.header.serialize().to_vec();
        let mut dir_entries: Vec<DirEntry> = doc_entries_extra;
        let mut record = |ctype: u32, oid: u64, off: u64, total: u64| {
            if oid != 0 {
                dir_entries.push(DirEntry { object_id: oid, chunk_type: ctype, file_offset: off, total_len: total });
            }
        };
        for (ctype, ver, flags, oid, payload) in &self.chunks {
            let off = out.len() as u64;
            let c = Chunk::build(*ctype, *ver, *flags, *oid, payload.clone());
            let b = c.to_bytes();
            record(*ctype, *oid, off, b.len() as u64);
            out.extend_from_slice(&b);
        }
        for raw in &self.preserved {
            // Trust-but-verify: must at least parse as a chunk header so we
            // never preserve framing garbage.
            let h = ChunkHeader::parse(raw).expect("preserved bytes must be a valid chunk");
            assert!(!h.is_critical(), "only ancillary chunks may be preserved blindly");
            let off = out.len() as u64;
            if h.object_id != 0 {
                record(h.chunk_type, h.object_id, off, raw.len() as u64);
            }
            out.extend_from_slice(raw);
        }
        dir_entries.sort_by_key(|e| e.object_id);
        let dir = ObjectDirectory { txn_id: 1, entries: dir_entries };
        let dir_payload = dir.serialize();
        let dir_off = out.len() as u64;
        let dir_chunk = Chunk::build(T_OBJECT_DIRECTORY, 1, FLAG_CRITICAL, 0, dir_payload);
        let dir_bytes = dir_chunk.to_bytes();
        out.extend_from_slice(&dir_bytes);
        let mut sec_refs: Vec<SecondaryRef> = Vec::new();
        // Secondary index chunks live between directory and root so the
        // root can point at them; they carry no object_id (never indexed).
        // Directory order is irrelevant — only offsets matter.
        for (itype, payload) in &self.indices {
            let off = out.len() as u64;
            let b = Chunk::build(T_INDEX_SECONDARY, 1, 0, 0, payload.clone()).to_bytes();
            out.extend_from_slice(&b);
            sec_refs.push(SecondaryRef { index_type: *itype, index_offset: off, index_len: b.len() as u64 });
        }
        let root = RootIndex { dir_offset: dir_off, dir_len: dir_bytes.len() as u64, secondary: sec_refs };
        let root_payload = root.serialize();
        let root_off = out.len() as u64;
        let root_chunk = Chunk::build(T_ROOT_INDEX, 1, FLAG_CRITICAL, 0, root_payload);
        let root_bytes = root_chunk.to_bytes();
        out.extend_from_slice(&root_bytes);
        let footer = Footer {
            root_offset: root_off,
            root_len: root_bytes.len() as u64,
            txn_id: 1,
            prev_footer_offset: 0,
            compacted: false,
        };
        out.extend_from_slice(&footer.serialize());
        out
    }
}

/// Append-only commit: returns new file bytes = old bytes + new chunks +
/// new directory (full snapshot) + new root + new footer (txn+1).
/// Unknown ancillary chunks found in the old file are automatically
/// carried (their directory entries re-point at the OLD offsets — bytes
/// are never copied, only re-indexed).
pub fn append_commit(
    old_raw: &[u8],
    updated_objects: Vec<(u64, Vec<u8>)>,
    new_objects: Vec<(u64, Vec<u8>)>,
    tombstones: Vec<u64>,
) -> BmoResult<Vec<u8>> {
    let base = BmoFile::open_bytes(old_raw.to_vec())?;
    let mut out = old_raw.to_vec();
    // Live map carried from previous directory.
    let mut live: HashMap<u64, (u32, u64, u64)> = HashMap::new();
    for e in &base.dir.entries {
        if e.file_offset == crate::directory::TOMBSTONE {
            continue;
        }
        live.insert(e.object_id, (e.chunk_type, e.file_offset, e.total_len));
    }
    for t in tombstones {
        live.remove(&t);
    }
    // Overwrites: same object_id, new chunk bytes appended.
    for (oid, payload) in updated_objects {
        let c = Chunk::build(T_GENERIC_OBJECT, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, oid, payload);
        let b = c.to_bytes();
        let off = out.len() as u64;
        out.extend_from_slice(&b);
        live.insert(oid, (T_GENERIC_OBJECT, off, b.len() as u64));
    }
    for (oid, payload) in new_objects {
        if live.contains_key(&oid) {
            return Err(BmoError::DuplicateObjectId);
        }
        let c = Chunk::build(T_GENERIC_OBJECT, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, oid, payload);
        let b = c.to_bytes();
        let off = out.len() as u64;
        out.extend_from_slice(&b);
        live.insert(oid, (T_GENERIC_OBJECT, off, b.len() as u64));
    }
    let mut entries: Vec<DirEntry> = live
        .into_iter()
        .map(|(oid, (ctype, fo, tl))| DirEntry { object_id: oid, chunk_type: ctype, file_offset: fo, total_len: tl })
        .collect();
    // NOTE (v0.1): tombstoned IDs are dropped from the directory snapshot.
    // RFC-0001 section 7 RECOMMENDS keeping TOMBSTONE entries for sync;
    // that becomes MUST in the collaboration profile (later RFC).
    entries.sort_by_key(|e| e.object_id);
    let txn = base.footer.txn_id + 1;
    let dir = ObjectDirectory { txn_id: txn, entries };
    let dir_off = out.len() as u64;
    let dir_bytes = Chunk::build(T_OBJECT_DIRECTORY, 1, FLAG_CRITICAL, 0, dir.serialize()).to_bytes();
    out.extend_from_slice(&dir_bytes);
    let root = RootIndex { dir_offset: dir_off, dir_len: dir_bytes.len() as u64, secondary: vec![] };
    let root_off = out.len() as u64;
    let root_bytes = Chunk::build(T_ROOT_INDEX, 1, FLAG_CRITICAL, 0, root.serialize()).to_bytes();
    out.extend_from_slice(&root_bytes);
    let footer = Footer {
        root_offset: root_off,
        root_len: root_bytes.len() as u64,
        txn_id: txn,
        prev_footer_offset: base.footer_offset,
        compacted: false,
    };
    out.extend_from_slice(&footer.serialize());
    // New file must open cleanly (fail fast on builder bugs).
    let _ = BmoFile::open_bytes(out.clone())?;
    Ok(out)
}

/// Deterministic hello.bmo builder (mirrors tests/vectors/make_hello.py).
pub fn build_hello(file_id: [u8; 16]) -> Vec<u8> {
    use crate::cbor::Value::*;
    let doc = cbor::encode(&Map(vec![
        (cbor::text("bmoMajor"), UInt(0)),
        (cbor::text("bmoMinor"), UInt(1)),
        (cbor::text("created"), UInt(0)),
        (cbor::text("docId"), cbor::bytes(&file_id)),
        (cbor::text("docType"), cbor::text("generic")),
        (cbor::text("features"), Array(vec![])),
        (cbor::text("metadata"), Map(vec![(cbor::text("title"), cbor::text("Hello BMO"))])),
        (cbor::text("minMajor"), UInt(0)),
        (cbor::text("minMinor"), UInt(1)),
        (cbor::text("modified"), UInt(0)),
        (cbor::text("optionalExtensions"), Array(vec![])),
        (cbor::text("requiredExtensions"), Array(vec![])),
    ]));
    let o1 = cbor::encode(&Map(vec![
        (cbor::text("body"), Map(vec![(cbor::text("title"), cbor::text("Hello BMO"))])),
        (cbor::text("kind"), cbor::text("document")),
        (cbor::text("parent"), UInt(0)),
    ]));
    let o2 = cbor::encode(&Map(vec![
        (cbor::text("body"), Map(vec![(cbor::text("number"), UInt(1))])),
        (cbor::text("kind"), cbor::text("page")),
        (cbor::text("parent"), UInt(1)),
    ]));
    let o3 = cbor::encode(&Map(vec![
        (
            cbor::text("body"),
            Map(vec![
                (cbor::text("lang"), cbor::text("en")),
                (cbor::text("text"), cbor::text("Hello BMO")),
            ]),
        ),
        (cbor::text("kind"), cbor::text("text")),
        (cbor::text("parent"), UInt(2)),
    ]));
    let mut b = FileBuilder::new(file_id);
    b.document(doc);
    b.object(1, o1);
    b.object(2, o2);
    b.object(3, o3);
    b.finish(vec![])
}
