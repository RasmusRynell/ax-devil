//! A live source never ends its input, so a decoder that can take one gives its fixed header before any record.

use foundation::CancelToken;
use handler_api::{Framing, Header, Settings};

mod common;

#[test]
fn every_records_decoder_gives_its_header_before_reading_a_record() {
    let records: Vec<_> = scene_decoders::decoders()
        .into_iter()
        .filter(|decoder| decoder.spec().framing == Framing::Records)
        .collect();
    assert!(!records.is_empty());
    for decoder in records {
        let (gave, result) = common::run_input(
            &*decoder,
            &mut common::Memory(Vec::new()),
            &Settings::new(),
            &CancelToken::new(),
        );
        let summary = result.expect("an empty input decodes");
        let id = &decoder.spec().id;
        assert_eq!(
            gave.header,
            Some(Header {
                filter: summary.filter.clone(),
                coverage: summary.coverage,
                metadata: summary.metadata,
            }),
            "{id}"
        );
        assert!(!summary.filter.options.is_empty(), "{id}");
    }
}
