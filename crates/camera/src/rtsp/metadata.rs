//! Rebuilds ONVIF metadata documents from RTP packets. Like the video assembler, it never emits a damaged one.

use bytes::{BufMut, Bytes, BytesMut};

use super::packet::Packet;
use crate::live::CaptureTime;

/// The most bytes one metadata document may grow to.
const MAX_DOCUMENT: usize = 8 << 20;

/// What the metadata assembler hands out.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Piece {
    /// A whole document.
    Doc {
        capture: Option<CaptureTime>,
        xml: Bytes,
    },
    /// A document was lost (to missing packets or its size), so the reader must be told.
    Lost,
}

/// Builds the ONVIF metadata documents of one stream: a document ends at the RTP marker bit. One that lost packets
/// or outgrew its limit is discarded through its marker and reported as [`Piece::Lost`], never emitted in part. A
/// loss between two documents cannot be told from a loss at the start of the next one, so that one is discarded too;
/// and when the lost packets include a marker, the document that follows is swallowed with the damaged one.
pub(crate) struct MetadataAssembler {
    data: BytesMut,
    capture: Option<CaptureTime>,
    damaged: bool,
    limit: usize,
}

impl MetadataAssembler {
    pub(crate) fn new() -> Self {
        Self {
            data: BytesMut::new(),
            capture: None,
            damaged: false,
            limit: MAX_DOCUMENT,
        }
    }

    /// Adds one packet; appends what it finishes to `out`.
    pub(crate) fn push(&mut self, packet: &Packet, out: &mut Vec<Piece>) {
        if packet.loss > 0 {
            self.damaged = true;
        }
        if packet.capture.is_some() {
            self.capture = packet.capture;
        }
        if !self.damaged {
            self.data.put_slice(&packet.payload);
            if self.data.len() > self.limit {
                self.damaged = true;
                self.data = BytesMut::new();
            }
        }
        if packet.marker {
            let (capture, damaged) = (self.capture, self.damaged);
            let xml = std::mem::take(&mut self.data).freeze();
            self.reset();
            if damaged {
                out.push(Piece::Lost);
            } else if !xml.is_empty() {
                out.push(Piece::Doc { capture, xml });
            }
        }
    }

    fn reset(&mut self) {
        self.data.clear();
        self.capture = None;
        self.damaged = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(timestamp: u32, marker: bool, payload: &[u8]) -> Packet {
        Packet {
            marker,
            timestamp,
            loss: 0,
            capture: None,
            payload: Bytes::copy_from_slice(payload),
        }
    }

    fn feed(packets: &[Packet]) -> Vec<Piece> {
        let mut assembler = MetadataAssembler::new();
        let mut out = Vec::new();
        for packet in packets {
            assembler.push(packet, &mut out);
        }
        out
    }

    fn doc(xml: &'static [u8]) -> Piece {
        Piece::Doc {
            capture: None,
            xml: Bytes::from_static(xml),
        }
    }

    #[test]
    fn metadata_documents_end_at_the_marker() {
        let mut first = packet(1, false, b"<tt:Frame>");
        first.capture = Some(CaptureTime(5));
        let out = feed(&[
            first,
            packet(1, true, b"</tt:Frame>"),
            packet(2, true, b"<tt:Frame/>"),
        ]);
        assert_eq!(
            out,
            [
                Piece::Doc {
                    capture: Some(CaptureTime(5)),
                    xml: Bytes::from_static(b"<tt:Frame></tt:Frame>")
                },
                doc(b"<tt:Frame/>"),
            ]
        );
    }

    #[test]
    fn a_document_missing_a_middle_packet_is_discarded_through_its_marker() {
        let mut after_gap = packet(1, false, b"middle");
        after_gap.loss = 1;
        let out = feed(&[
            packet(1, false, b"<tt:Frame>"),
            after_gap,
            packet(1, true, b"</tt:Frame>"),
            packet(2, true, b"<tt:Frame/>"),
        ]);
        assert_eq!(
            out,
            [Piece::Lost, doc(b"<tt:Frame/>")],
            "no suffix is passed off as a document"
        );
    }

    #[test]
    fn an_oversized_document_is_reported_lost_and_the_next_one_arrives() {
        let mut assembler = MetadataAssembler::new();
        assembler.limit = 16;
        let mut out = Vec::new();
        for p in [
            packet(1, false, &[b'x'; 20]),
            packet(1, true, b"</tt:Frame>"),
            packet(2, true, b"<tt:Frame/>"),
        ] {
            assembler.push(&p, &mut out);
        }
        assert_eq!(out, [Piece::Lost, doc(b"<tt:Frame/>")]);
    }
}
