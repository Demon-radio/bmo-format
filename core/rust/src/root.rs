//! Root Index (binary) — RFC-0001 section 8.
//! Payload: u32 version, u32 index_count(M), u64 dir_offset, u64 dir_len,
//! then M * (u32 index_type, u32 reserved, u64 index_offset, u64 index_len).

use crate::error::{BmoError, BmoResult};

pub const ROOT_VERSION: u32 = 1;
pub const MAX_SECONDARY: usize = 255;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecondaryRef {
    pub index_type: u32,
    pub index_offset: u64,
    pub index_len: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RootIndex {
    pub dir_offset: u64,
    pub dir_len: u64,
    pub secondary: Vec<SecondaryRef>,
}

impl RootIndex {
    pub fn parse(payload: &[u8]) -> BmoResult<Self> {
        if payload.len() < 24 {
            return Err(BmoError::Corrupt);
        }
        let ver = u32::from_le_bytes(payload[0..4].try_into().unwrap());
        let m = u32::from_le_bytes(payload[4..8].try_into().unwrap()) as usize;
        let dir_offset = u64::from_le_bytes(payload[8..16].try_into().unwrap());
        let dir_len = u64::from_le_bytes(payload[16..24].try_into().unwrap());
        if ver != ROOT_VERSION {
            return Err(BmoError::UpgradeRequired);
        }
        if m > MAX_SECONDARY || payload.len() != 24 + m * 24 {
            return Err(BmoError::Corrupt);
        }
        let mut secondary = Vec::with_capacity(m);
        for i in 0..m {
            let o = 24 + i * 24;
            let index_type = u32::from_le_bytes(payload[o..o + 4].try_into().unwrap());
            let reserved = u32::from_le_bytes(payload[o + 4..o + 8].try_into().unwrap());
            let index_offset = u64::from_le_bytes(payload[o + 8..o + 16].try_into().unwrap());
            let index_len = u64::from_le_bytes(payload[o + 16..o + 24].try_into().unwrap());
            if reserved != 0 {
                return Err(BmoError::Corrupt);
            }
            // Unknown index types are preserved by the caller (chunk bytes),
            // ignored here — never fatal (RFC-0001 section 8).
            secondary.push(SecondaryRef { index_type, index_offset, index_len });
        }
        Ok(Self { dir_offset, dir_len, secondary })
    }

    pub fn serialize(&self) -> Vec<u8> {
        // NOTE: each secondary entry is 24 bytes (4+4+8+8); parse stride must match.
        let mut out = Vec::with_capacity(24 + self.secondary.len() * 24);
        out.extend_from_slice(&ROOT_VERSION.to_le_bytes());
        out.extend_from_slice(&(self.secondary.len() as u32).to_le_bytes());
        out.extend_from_slice(&self.dir_offset.to_le_bytes());
        out.extend_from_slice(&self.dir_len.to_le_bytes());
        for s in &self.secondary {
            out.extend_from_slice(&s.index_type.to_le_bytes());
            out.extend_from_slice(&0u32.to_le_bytes());
            out.extend_from_slice(&s.index_offset.to_le_bytes());
            out.extend_from_slice(&s.index_len.to_le_bytes());
        }
        out
    }
}
