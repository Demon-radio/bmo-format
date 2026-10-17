//! File header (64 bytes) — RFC-0001 section 4.

use crate::crc32c::crc32c;
use crate::error::{BmoError, BmoResult};

pub const HEADER_LEN: usize = 64;
pub const MAGIC: [u8; 4] = [0x42, 0x4D, 0x4F, 0x1A];
pub const HEADER_VERSION: u16 = 1;

pub const FLAG_ENCRYPTED: u32 = 0x0000_0001;
pub const FLAG_SIGNED: u32 = 0x0000_0002;
pub const FLAG_ARCHIVE: u32 = 0x0000_0004;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileHeader {
    pub major: u16,
    pub minor: u16,
    pub flags: u32,
    pub file_id: [u8; 16],
    pub created_ms: i64,
}

impl FileHeader {
    pub fn parse(data: &[u8]) -> BmoResult<Self> {
        if data.len() < HEADER_LEN {
            return Err(BmoError::TooSmall);
        }
        if data[0..4] != MAGIC {
            return Err(BmoError::BadMagic);
        }
        let major = u16::from_le_bytes([data[4], data[5]]);
        let minor = u16::from_le_bytes([data[6], data[7]]);
        let hlen = u16::from_le_bytes([data[8], data[9]]);
        let hver = u16::from_le_bytes([data[10], data[11]]);
        if hlen as usize != HEADER_LEN || hver != HEADER_VERSION {
            return Err(BmoError::BadHeaderLength);
        }
        // v0.x experimental gate: only 0.1 accepted by this build.
        if major != 0 || minor != 1 {
            if major != 0 {
                return Err(BmoError::IncompatibleMajor);
            }
            return Err(BmoError::UpgradeRequired);
        }
        let flags = u32::from_le_bytes([data[12], data[13], data[14], data[15]]);
        let mut file_id = [0u8; 16];
        file_id.copy_from_slice(&data[16..32]);
        let stored = u32::from_le_bytes([data[32], data[33], data[34], data[35]]);
        let mut chk = Vec::with_capacity(HEADER_LEN);
        chk.extend_from_slice(&data[0..32]);
        chk.extend_from_slice(&data[36..64]);
        if crc32c(&chk) != stored {
            return Err(BmoError::HeaderCrc);
        }
        let created_ms = i64::from_le_bytes(data[36..44].try_into().unwrap());
        // bytes 44..64 reserved: MUST be zero on write; ignored on read.
        Ok(Self { major, minor, flags, file_id, created_ms })
    }

    pub fn serialize(&self) -> [u8; HEADER_LEN] {
        let mut out = [0u8; HEADER_LEN];
        out[0..4].copy_from_slice(&MAGIC);
        out[4..6].copy_from_slice(&self.major.to_le_bytes());
        out[6..8].copy_from_slice(&self.minor.to_le_bytes());
        out[8..10].copy_from_slice(&(HEADER_LEN as u16).to_le_bytes());
        out[10..12].copy_from_slice(&HEADER_VERSION.to_le_bytes());
        out[12..16].copy_from_slice(&self.flags.to_le_bytes());
        out[16..32].copy_from_slice(&self.file_id);
        out[36..44].copy_from_slice(&self.created_ms.to_le_bytes());
        // 44..64 stay zero.
        let mut chk = Vec::with_capacity(HEADER_LEN);
        chk.extend_from_slice(&out[0..32]);
        chk.extend_from_slice(&out[36..64]);
        out[32..36].copy_from_slice(&crc32c(&chk).to_le_bytes());
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip() {
        let h = FileHeader { major: 0, minor: 1, flags: 0, file_id: [7u8; 16], created_ms: 0 };
        let b = h.serialize();
        assert_eq!(FileHeader::parse(&b).unwrap(), h);
        let mut bad = b;
        bad[20] ^= 0xFF;
        assert_eq!(FileHeader::parse(&bad).unwrap_err(), BmoError::HeaderCrc);
    }
}
