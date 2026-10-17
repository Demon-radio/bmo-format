//! Footer (64 bytes) + recovery scan — RFC-0001 section 12.

use crate::crc32c::crc32c;
use crate::error::{BmoError, BmoResult};

pub const FOOTER_LEN: usize = 64;
pub const FOOTER_MAGIC: [u8; 4] = [0x42, 0x4D, 0x4F, 0x46]; // "BMOF"
pub const FOOTER_VERSION: u16 = 1;
/// Backward-scan window for torn-tail recovery (section 12.2).
pub const RECOVERY_WINDOW: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Footer {
    pub root_offset: u64,
    pub root_len: u64,
    pub txn_id: u64,
    pub prev_footer_offset: u64,
    pub compacted: bool,
}

impl Footer {
    pub fn parse(data: &[u8]) -> BmoResult<Self> {
        if data.len() < FOOTER_LEN {
            return Err(BmoError::TooSmall);
        }
        if data[0..4] != FOOTER_MAGIC || data[60..64] != FOOTER_MAGIC {
            return Err(BmoError::NoValidFooter);
        }
        let ver = u16::from_le_bytes([data[4], data[5]]);
        let flags = u16::from_le_bytes([data[6], data[7]]);
        if ver != FOOTER_VERSION {
            return Err(BmoError::UpgradeRequired);
        }
        let root_offset = u64::from_le_bytes(data[8..16].try_into().unwrap());
        let root_len = u64::from_le_bytes(data[16..24].try_into().unwrap());
        let txn_id = u64::from_le_bytes(data[24..32].try_into().unwrap());
        let prev = u64::from_le_bytes(data[32..40].try_into().unwrap());
        let stored = u32::from_le_bytes(data[40..44].try_into().unwrap());
        if crc32c(&data[0..40]) != stored {
            return Err(BmoError::FooterCrc);
        }
        if txn_id == 0 {
            return Err(BmoError::Corrupt);
        }
        Ok(Self {
            root_offset,
            root_len,
            txn_id,
            prev_footer_offset: prev,
            compacted: flags & 0x0001 != 0,
        })
    }

    pub fn serialize(&self) -> [u8; FOOTER_LEN] {
        let mut out = [0u8; FOOTER_LEN];
        out[0..4].copy_from_slice(&FOOTER_MAGIC);
        out[4..6].copy_from_slice(&FOOTER_VERSION.to_le_bytes());
        out[6..8].copy_from_slice(&(if self.compacted { 1u16 } else { 0u16 }).to_le_bytes());
        out[8..16].copy_from_slice(&self.root_offset.to_le_bytes());
        out[16..24].copy_from_slice(&self.root_len.to_le_bytes());
        out[24..32].copy_from_slice(&self.txn_id.to_le_bytes());
        out[32..40].copy_from_slice(&self.prev_footer_offset.to_le_bytes());
        let c = crc32c(&out[0..40]);
        out[40..44].copy_from_slice(&c.to_le_bytes());
        // 44..60 reserved zero; 60..64 duplicate magic.
        out[60..64].copy_from_slice(&FOOTER_MAGIC);
        out
    }
}

/// Find the newest valid footer: fast path at EOF, else byte-wise
/// backward scan within RECOVERY_WINDOW (torn-tail case).
pub fn find_footer(file: &[u8]) -> BmoResult<(u64, Footer)> {
    if file.len() < 64 + 64 {
        return Err(BmoError::TooSmall);
    }
    let end = file.len();
    if let Ok(f) = Footer::parse(&file[end - FOOTER_LEN..end]) {
        return Ok(((end - FOOTER_LEN) as u64, f));
    }
    let lo = end.saturating_sub(RECOVERY_WINDOW).saturating_sub(FOOTER_LEN);
    let mut off = end.saturating_sub(FOOTER_LEN + 1);
    while off >= lo {
        if file[off..].len() >= FOOTER_LEN
            && file[off..off + 4] == FOOTER_MAGIC
            && file[off + 60..off + 64] == FOOTER_MAGIC
            && Footer::parse(&file[off..off + FOOTER_LEN]).is_ok()
        {
            let f = Footer::parse(&file[off..off + FOOTER_LEN]).unwrap();
            return Ok((off as u64, f));
        }
        if off == 0 {
            break;
        }
        off -= 1;
    }
    Err(BmoError::NoValidFooter)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn footer_roundtrip() {
        let f = Footer { root_offset: 697, root_len: 68, txn_id: 1, prev_footer_offset: 0, compacted: false };
        let b = f.serialize();
        assert_eq!(Footer::parse(&b).unwrap(), f);
    }
}
