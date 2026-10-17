//! CRC32C (Castagnoli) — RFC-0001 section 2.2.
//! Polynomial 0x1EDC6F41, reflected 0x82F63B78, init/xor 0xFFFFFFFF.
//! Check: CRC32C("123456789") == 0xE3069283, CRC32C("") == 0.

const POLY: u32 = 0x82F6_3B78;

const fn build_table() -> [u32; 256] {
    let mut t = [0u32; 256];
    let mut i = 0usize;
    while i < 256 {
        let mut crc = i as u32;
        let mut k = 0;
        while k < 8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ POLY;
            } else {
                crc >>= 1;
            }
            k += 1;
        }
        t[i] = crc;
        i += 1;
    }
    t
}

const TABLE: [u32; 256] = build_table();

/// CRC32C over `data`.
pub fn crc32c(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &b in data {
        crc = TABLE[((crc ^ b as u32) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_vector() {
        assert_eq!(crc32c(b"123456789"), 0xE3069283);
        assert_eq!(crc32c(b""), 0x00000000);
    }
}
