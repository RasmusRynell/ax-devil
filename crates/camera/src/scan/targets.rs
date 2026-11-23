//! The addresses a scan visits, from the text a person types.

use std::net::{Ipv4Addr, UdpSocket};

use crate::error::{CameraError, ErrorKind};

/// The most addresses one scan may visit.
pub const MAX_TARGETS: usize = 4096;

/// The IPv4 addresses to scan, in address order and without repeats; never empty.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Targets(Vec<Ipv4Addr>);

impl Targets {
    /// The addresses `text` names. It is addresses and ranges, separated by commas or spaces: `192.168.1.7`,
    /// `192.168.1.0/24`, or a range with a netmask or hostmask (`10.0.0.0/255.255.255.0`, `10.0.0.0/0.0.0.255`). A
    /// range of a prefix up to 30 leaves out its network and broadcast addresses. Blank text names the local network:
    /// the /24 around this computer's address on its default route.
    ///
    /// Anything else, or more than [`MAX_TARGETS`] addresses, is [`ErrorKind::InvalidTarget`]. Ranges are counted
    /// before they are expanded, so a huge one is refused cheaply.
    pub fn parse(text: &str) -> Result<Self, CameraError> {
        let mut spans = Vec::new();
        for token in text
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|token| !token.is_empty())
        {
            spans.push(span(token).ok_or_else(|| {
                CameraError::new(
                    ErrorKind::InvalidTarget,
                    format!("{token} is not an IPv4 address or range such as 192.168.1.0/24"),
                )
            })?);
        }
        if spans.is_empty() {
            spans.push(local_network()?);
        }
        spans.sort_unstable();
        let mut merged: Vec<(u32, u32)> = Vec::new();
        for (first, last) in spans {
            match merged.last_mut() {
                Some((_, end)) if first <= end.saturating_add(1) => *end = (*end).max(last),
                _ => merged.push((first, last)),
            }
        }
        let count: u64 = merged
            .iter()
            .map(|(first, last)| u64::from(last - first) + 1)
            .sum();
        if count > MAX_TARGETS as u64 {
            return Err(CameraError::new(
                ErrorKind::InvalidTarget,
                format!("enter at most {MAX_TARGETS} addresses"),
            ));
        }
        Ok(Self(
            merged
                .into_iter()
                .flat_map(|(first, last)| first..=last)
                .map(Ipv4Addr::from)
                .collect(),
        ))
    }

    /// The addresses, in order.
    pub fn addresses(&self) -> &[Ipv4Addr] {
        &self.0
    }
}

/// The first and last address of `token`, an address with an optional prefix length, netmask or hostmask.
fn span(token: &str) -> Option<(u32, u32)> {
    let (address, mask) = match token.split_once('/') {
        Some((address, mask)) => (address, Some(mask)),
        None => (token, None),
    };
    let address = u32::from(address.parse::<Ipv4Addr>().ok()?);
    let prefix = mask.map_or(Some(32), prefix)?;
    let mask = u32::MAX.checked_shl(32 - prefix).unwrap_or(0);
    let (first, last) = (address & mask, address | !mask);
    // A prefix of 31 or 32 has no network or broadcast address.
    Some(if prefix >= 31 {
        (first, last)
    } else {
        (first + 1, last - 1)
    })
}

/// The prefix length a mask names: digits, a netmask (`255.255.255.0`) or a hostmask (`0.0.0.255`).
fn prefix(mask: &str) -> Option<u32> {
    if !mask.is_empty() && mask.bytes().all(|b| b.is_ascii_digit()) {
        return mask.parse().ok().filter(|prefix| *prefix <= 32);
    }
    let bits = u32::from(mask.parse::<Ipv4Addr>().ok()?);
    if bits.leading_ones() + bits.trailing_zeros() == 32 {
        Some(bits.leading_ones())
    } else if bits.leading_zeros() + bits.trailing_ones() == 32 {
        Some(bits.leading_zeros())
    } else {
        None
    }
}

/// The host addresses of the /24 around this computer's address on its default route.
fn local_network() -> Result<(u32, u32), CameraError> {
    let unknown = || {
        CameraError::new(
            ErrorKind::Unreachable,
            "this computer's network address is unknown",
        )
    };
    // Connecting a UDP socket only picks the outgoing interface; nothing is sent.
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).map_err(|_| unknown())?;
    socket
        .connect((Ipv4Addr::new(192, 0, 2, 1), 9))
        .map_err(|_| unknown())?;
    match socket.local_addr().map_err(|_| unknown())?.ip() {
        std::net::IpAddr::V4(address) => {
            let network = u32::from(address) & 0xFFFF_FF00;
            Ok((network + 1, network + 254))
        }
        std::net::IpAddr::V6(_) => Err(unknown()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn addresses(text: &str) -> Vec<String> {
        Targets::parse(text)
            .unwrap()
            .addresses()
            .iter()
            .map(ToString::to_string)
            .collect()
    }

    #[test]
    fn the_accepted_syntax_matches_the_python_scan() {
        let table: [(&str, &[&str]); 8] = [
            ("10.0.0.5", &["10.0.0.5"]),
            ("10.0.0.5/32", &["10.0.0.5"]),
            ("10.0.0.4/31", &["10.0.0.4", "10.0.0.5"]),
            ("10.0.0.0/30", &["10.0.0.1", "10.0.0.2"]),
            // Host bits are masked, not refused.
            ("10.0.0.6/30", &["10.0.0.5", "10.0.0.6"]),
            (
                "10.0.0.4/255.255.255.252, 10.0.0.5",
                &["10.0.0.5", "10.0.0.6"],
            ),
            ("10.0.0.0/0.0.0.3", &["10.0.0.1", "10.0.0.2"]),
            // Commas and spaces separate; overlaps and repeats merge, in address order.
            (
                "10.0.0.9,10.0.0.1 10.0.0.9\n10.0.0.1/32",
                &["10.0.0.1", "10.0.0.9"],
            ),
        ];
        for (text, expected) in table {
            assert_eq!(addresses(text), expected, "{text:?}");
        }
        assert_eq!(addresses("192.168.1.0/24").len(), 254);
        assert_eq!(
            addresses("10.0.0.0/24 10.0.0.128/25 10.0.1.0/24").len(),
            508
        );
    }

    #[test]
    fn other_text_and_more_than_4096_addresses_are_refused() {
        for bad in [
            "cam.local",
            "10.0.0",
            "10.0.0.256",
            "10.0.0.1/33",
            "10.0.0.1/",
            "10.0.0.1/255.0.255.0",
            "10.0.0.1-10.0.0.9",
            "::1",
            "10.0.0.1:80",
            "10.0.0.0/19",
            "10.0.0.0/0",
        ] {
            assert_eq!(
                Targets::parse(bad).unwrap_err().kind,
                ErrorKind::InvalidTarget,
                "{bad:?}"
            );
        }
        // 4094 addresses are fine; the 4096th (a /20 has 4094 hosts) plus two more are not.
        assert_eq!(addresses("10.0.0.0/20").len(), 4094);
        assert!(Targets::parse("10.0.0.0/20 10.1.0.1 10.1.0.2 10.1.0.3").is_err());
        assert_eq!(addresses("10.0.0.0/20 10.1.0.1 10.1.0.2").len(), 4096);
    }
}
