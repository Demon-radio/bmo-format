//! Object Directory (binary) — RFC-0001 section 7.
//! Payload: u32 version, u32 count, u64 txn, then count * 32-byte entries
//! (object_id u64, chunk_type u32, reserved u32, file_offset u64, total_len u64).
//! Entries MUST be sorted by object_id with no duplicates.

use crate::error::{BmoError, BmoResult};

pub const DIR_VERSION: u32 = 1;
pub const DIR_ENTRY_LEN: usize = 32;
pub const TOMBSTONE: u64 = 0xFFFF_FFFF_FFFF_FFFF;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DirEntry {
    pub object_id: u64,
    pub chunk_type: u32,
    pub file_offset: u64,
    pub total_len: u64,
}

#[derive(Debug, Clone)]
pub struct ObjectDirectory {
    pub txn_id: u64,
    pub entries: Vec<DirEntry>,
}

impl ObjectDirectory {
    pub fn parse(payload: &[u8]) -> BmoResult<Self> {
        if payload.len() < 16 {
            return Err(BmoError::Corrupt);
        }
        let ver = u32::from_le_bytes(payload[0..4].try_into().unwrap());
        let count = u32::from_le_bytes(payload[4..8].try_into().unwrap()) as usize;
        let txn_id = u64::from_le_bytes(payload[8..16].try_into().unwrap());
        if ver != DIR_VERSION {
            return Err(BmoError::UpgradeRequired);
        }
        if count > 0xFFFF_FFFFusize || payload.len() != 16 + count * DIR_ENTRY_LEN {
            return Err(BmoError::Corrupt);
        }
        let mut entries = Vec::with_capacity(count);
        let mut prev: Option<u64> = None;
        for i in 0..count {
            let o = 16 + i * DIR_ENTRY_LEN;
            let object_id = u64::from_le_bytes(payload[o..o + 8].try_into().unwrap());
            let chunk_type = u32::from_le_bytes(payload[o + 8..o + 12].try_into().unwrap());
            let reserved = u32::from_le_bytes(payload[o + 12..o + 16].try_into().unwrap());
            let file_offset = u64::from_le_bytes(payload[o + 16..o + 24].try_into().unwrap());
            let total_len = u64::from_le_bytes(payload[o + 24..o + 32].try_into().unwrap());
            if reserved != 0 || object_id == 0 || chunk_type == 0 {
                return Err(BmoError::Corrupt);
            }
            if let Some(p) = prev {
                if object_id <= p {
                    // Covers both unsorted and duplicate cases; duplicates
                    // get the precise code below when equal.
                    if object_id == p {
                        return Err(BmoError::DuplicateObjectId);
                    }
                    return Err(BmoError::DirectoryUnsorted);
                }
            }
            prev = Some(object_id);
            entries.push(DirEntry { object_id, chunk_type, file_offset, total_len });
        }
        Ok(Self { txn_id, entries })
    }

    pub fn serialize(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(16 + self.entries.len() * DIR_ENTRY_LEN);
        out.extend_from_slice(&DIR_VERSION.to_le_bytes());
        out.extend_from_slice(&(self.entries.len() as u32).to_le_bytes());
        out.extend_from_slice(&self.txn_id.to_le_bytes());
        for e in &self.entries {
            out.extend_from_slice(&e.object_id.to_le_bytes());
            out.extend_from_slice(&e.chunk_type.to_le_bytes());
            out.extend_from_slice(&0u32.to_le_bytes());
            out.extend_from_slice(&e.file_offset.to_le_bytes());
            out.extend_from_slice(&e.total_len.to_le_bytes());
        }
        out
    }

    pub fn find(&self, object_id: u64) -> Option<&DirEntry> {
        self.entries.binary_search_by_key(&object_id, |e| e.object_id).ok().map(|i| &self.entries[i])
    }
}
