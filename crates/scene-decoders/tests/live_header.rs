//! A live source never ends its input, so a decoder that can take one gives its fixed header before any record.

use foundation::CancelToken;
use handler_api::{Header, Settings};

mod common;

#[test]
fn every_live_decoder_gives_its_header_before_reading_a_record() {
    let live: Vec<_> = scene_decoders::decoders()
        .into_iter()
        .filter(|decoder| decoder.reads_live())
        .collect();
    assert!(!live.is_empty());
    for decoder in live {
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

#[test]
fn adf_frames_take_a_live_source_and_consolidated_tracks_do_not() {
    let live: Vec<_> = scene_decoders::decoders()
        .into_iter()
        .filter(|decoder| decoder.spec().plugin.starts_with("adf") && decoder.reads_live())
        .map(|decoder| decoder.spec().id.clone())
        .collect();
    assert_eq!(live, ["ADF_BETA_FRAME", "ADF_V1_FRAME"]);
}
