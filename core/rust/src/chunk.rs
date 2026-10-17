//! Chunks — RFC-0001 section 5.
//! Fixed 40-byte header + stored payload + 4-byte CRC trailer.
//! Without the `codecs` feature this build executes plain (codec 0) storage
//! only and cleanly signals UPGRADE_REQUIRED for coded chunks. With the
//! `codecs` feature, ZSTD (decompress), AES-256-GCM and ChaCha20-Poly1305
//! (decrypt) payloads are executed with the exact Python-kit semantics
//! (decrypt-then-decode).

use crate::crc32c::crc32c;
use crate::error::{BmoError, BmoResult};

pub const CHUNK_HEADER_LEN: usize = 40;
pub const CHUNK_TRAILER_LEN: usize = 4;

pub const FLAG_COMPRESSED: u16 = 0x0001;
pub const FLAG_ENCRYPTED: u16 = 0x0002;
pub const FLAG_HAS_OBJECT_ID: u16 = 0x0004;
pub const FLAG_CRITICAL: u16 = 0x0008;

// Chunk types.
pub const T_DOCUMENT: u32 = 0x0001;
pub const T_OBJECT_DIRECTORY: u32 = 0x0002;
pub const T_ROOT_INDEX: u32 = 0x0003;
pub const T_ASSET_TABLE: u32 = 0x0004;
pub const T_ASSET_DATA: u32 = 0x0005;
pub const T_INDEX_SECONDARY: u32 = 0x0006;
pub const T_SECURITY: u32 = 0x0007;
pub const T_SIGNATURE: u32 = 0x0008;
pub const T_GENERIC_OBJECT: u32 = 0x0010;

// Codec IDs.
pub const COMP_NONE: u16 = 0;
pub const COMP_ZSTD: u16 = 1;
pub const COMP_DEFLATE: u16 = 2;
pub const ENC_NONE: u16 = 0;
pub const ENC_AES_GCM: u16 = 1;
pub const ENC_CHACHA20_POLY1305: u16 = 2;
pub const ENC_XCHACHA20_POLY1305: u16 = 3;

pub const MAX_CHUNK_LEN: u64 = 0xFFFF_FFFF;
pub const MAX_U48: u64 = 0x0000_FFFF_FFFF_FFFF;

/// Parsed chunk header (without payload).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkHeader {
    pub chunk_type: u32,
    pub version: u16,
    pub flags: u16,
    pub object_id: u64,
    pub stored_len: u64,
    pub decoded_len: u64,
    pub codec: u32,
}

impl ChunkHeader {
    pub fn compression_id(&self) -> u16 {
        (self.codec & 0xFFFF) as u16
    }
    pub fn encryption_id(&self) -> u16 {
        ((self.codec >> 16) & 0xFFFF) as u16
    }
    pub fn is_critical(&self) -> bool {
        self.flags & FLAG_CRITICAL != 0
    }

    pub fn parse(data: &[u8]) -> BmoResult<Self> {
        if data.len() < CHUNK_HEADER_LEN {
            return Err(BmoError::Truncated);
        }
        let h = Self {
            chunk_type: u32::from_le_bytes(data[0..4].try_into().unwrap()),
            version: u16::from_le_bytes(data[4..6].try_into().unwrap()),
            flags: u16::from_le_bytes(data[6..8].try_into().unwrap()),
            object_id: u64::from_le_bytes(data[8..16].try_into().unwrap()),
            stored_len: u64::from_le_bytes(data[16..24].try_into().unwrap()),
            decoded_len: u64::from_le_bytes(data[24..32].try_into().unwrap()),
            codec: u32::from_le_bytes(data[32..36].try_into().unwrap()),
        };
        let stored_crc = u32::from_le_bytes(data[36..40].try_into().unwrap());
        if crc32c(&data[0..36]) != stored_crc {
            return Err(BmoError::ChunkHeaderCrc);
        }
        if h.chunk_type == 0 {
            return Err(BmoError::Corrupt);
        }
        if h.stored_len > MAX_CHUNK_LEN || h.decoded_len > MAX_CHUNK_LEN {
            return Err(BmoError::LimitExceeded);
        }
        if h.flags & FLAG_HAS_OBJECT_ID == 0 && h.object_id != 0 {
            return Err(BmoError::Corrupt);
        }
        let comp = h.compression_id();
        let enc = h.encryption_id();
        let want_comp = h.flags & FLAG_COMPRESSED != 0;
        let want_enc = h.flags & FLAG_ENCRYPTED != 0;
        if want_comp != (comp != 0) || want_enc != (enc != 0) {
            return Err(BmoError::Corrupt);
        }
        // Codec registry (RFC-0001 section 9): 0=NONE always available.
        // Without the `codecs` feature nothing else is executable — coded
        // chunks are a clean UPGRADE signal, never a silent mis-decode.
        // With the feature, compression 1=ZSTD / 2=DEFLATE and encryption
        // 1=AES-256-GCM / 2=ChaCha20-Poly1305 / 3=XChaCha20-Poly1305
        // execute; unknown registry members
        // stay UPGRADE_REQUIRED (same as the Python kit).
        #[cfg(not(feature = "codecs"))]
        if comp != COMP_NONE || enc != ENC_NONE {
            return Err(BmoError::UpgradeRequired);
        }
        #[cfg(feature = "codecs")]
        if comp > COMP_DEFLATE || enc > ENC_XCHACHA20_POLY1305 {
            return Err(BmoError::UpgradeRequired);
        }
        if h.flags & 0xFFF0 != 0 {
            // Reserved flags: refuse if critical, else caller preserves.
            if h.is_critical() {
                return Err(BmoError::UnknownCritical);
            }
        }
        Ok(h)
    }

    pub fn serialize(&self) -> [u8; CHUNK_HEADER_LEN] {
        let mut out = [0u8; CHUNK_HEADER_LEN];
        out[0..4].copy_from_slice(&self.chunk_type.to_le_bytes());
        out[4..6].copy_from_slice(&self.version.to_le_bytes());
        out[6..8].copy_from_slice(&self.flags.to_le_bytes());
        out[8..16].copy_from_slice(&self.object_id.to_le_bytes());
        out[16..24].copy_from_slice(&self.stored_len.to_le_bytes());
        out[24..32].copy_from_slice(&self.decoded_len.to_le_bytes());
        out[32..36].copy_from_slice(&self.codec.to_le_bytes());
        let c = crc32c(&out[0..36]);
        out[36..40].copy_from_slice(&c.to_le_bytes());
        out
    }

    pub fn total_len(&self) -> BmoResult<u64> {
        self.stored_len
            .checked_add((CHUNK_HEADER_LEN + CHUNK_TRAILER_LEN) as u64)
            .ok_or(BmoError::LimitExceeded)
    }
}

/// A fully read chunk (plain v0.1: stored == decoded).
#[derive(Debug, Clone)]
pub struct Chunk {
    pub header: ChunkHeader,
    pub payload: Vec<u8>, // decoded bytes
    pub raw_stored: Vec<u8>,
}

impl Chunk {
    /// Caller guarantees `file[offset..]` is readable; validates header,
    /// payload CRC and bounds against `file_end` (footer start).
    /// Keyless variant of [`Chunk::read_at_with_key`].
    pub fn read_at(file: &[u8], offset: u64, file_end: u64) -> BmoResult<Self> {
        Self::read_at_with_key(file, offset, file_end, None)
    }

    /// Full read: header + payload CRC, then codec execution when the
    /// `codecs` feature is on. Read order is decrypt-then-decompress
    /// (writers compress first — RFC-0001 section 9).
    pub fn read_at_with_key(
        file: &[u8],
        offset: u64,
        file_end: u64,
        content_key: Option<&[u8; 32]>,
    ) -> BmoResult<Self> {
        if offset > MAX_U48 || file_end > file.len() as u64 {
            return Err(BmoError::LimitExceeded);
        }
        let off = offset as usize;
        let end = file_end as usize;
        if off + CHUNK_HEADER_LEN > end {
            return Err(BmoError::Truncated);
        }
        let header = ChunkHeader::parse(&file[off..off + CHUNK_HEADER_LEN])?;
        let total = header.total_len()? as usize;
        if off + total > end {
            return Err(BmoError::Truncated);
        }
        let stored = file[off + CHUNK_HEADER_LEN..off + CHUNK_HEADER_LEN + header.stored_len as usize].to_vec();
        let trailer = u32::from_le_bytes(
            file[off + CHUNK_HEADER_LEN + stored.len()..off + total].try_into().unwrap(),
        );
        if crc32c(&stored) != trailer {
            return Err(BmoError::PayloadCrc);
        }

        #[cfg(feature = "codecs")]
        let payload = {
            let mut payload = stored.clone();
            match header.encryption_id() {
                ENC_NONE => {}
                ENC_AES_GCM => {
                    let key = content_key.ok_or(BmoError::NeedKey)?;
                    payload = crate::codec::aes_gcm_decrypt(key, &payload)?;
                }
                ENC_CHACHA20_POLY1305 => {
                    let key = content_key.ok_or(BmoError::NeedKey)?;
                    payload = crate::codec::chacha20poly1305_decrypt(key, &payload)?;
                }
                ENC_XCHACHA20_POLY1305 => {
                    let key = content_key.ok_or(BmoError::NeedKey)?;
                    payload = crate::codec::xchacha20poly1305_decrypt(key, &payload)?;
                }
                // parse() already rejected every other registry member.
                _ => return Err(BmoError::UpgradeRequired),
            }
            if header.compression_id() == COMP_ZSTD {
                payload = crate::codec::zstd_decompress(&payload, header.decoded_len as usize)?;
            } else if header.compression_id() == COMP_DEFLATE {
                payload = crate::codec::deflate_decompress(&payload, header.decoded_len as usize)?;
            } else if payload.len() != header.decoded_len as usize {
                // plain: stored == decoded; encrypted-only: decrypted == decoded
                return Err(BmoError::Corrupt);
            }
            payload
        };
        #[cfg(not(feature = "codecs"))]
        let payload = {
            // Codec gate already ran in parse(); this build only sees plain.
            let _ = content_key;
            if header.stored_len != header.decoded_len {
                return Err(BmoError::Corrupt);
            }
            stored.clone()
        };

        Ok(Self { header, payload, raw_stored: stored })
    }

    /// Build a plain chunk for writing.
    pub fn build(chunk_type: u32, version: u16, flags: u16, object_id: u64, payload: Vec<u8>) -> Self {
        let header = ChunkHeader {
            chunk_type,
            version,
            flags,
            object_id,
            stored_len: payload.len() as u64,
            decoded_len: payload.len() as u64,
            codec: 0,
        };
        Self { header, payload: payload.clone(), raw_stored: payload }
    }

    /// On-disk bytes (header + stored + trailer).
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(CHUNK_HEADER_LEN + self.raw_stored.len() + 4);
        out.extend_from_slice(&self.header.serialize());
        out.extend_from_slice(&self.raw_stored);
        out.extend_from_slice(&crc32c(&self.raw_stored).to_le_bytes());
        out
    }
}

/// Chunk types this build understands. Unknown + critical => refuse;
/// unknown + ancillary => skip but preserve (caller keeps raw bytes).
pub fn is_known_type(t: u32) -> bool {
    matches!(
        t,
        T_DOCUMENT
            | T_OBJECT_DIRECTORY
            | T_ROOT_INDEX
            | T_ASSET_TABLE
            | T_ASSET_DATA
            | T_INDEX_SECONDARY
            | T_SECURITY
            | T_SIGNATURE
            | T_GENERIC_OBJECT
    )
}
