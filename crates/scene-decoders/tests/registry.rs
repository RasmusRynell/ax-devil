//! The decoder registry lists each built-in once and describes it consistently.

use std::collections::HashSet;

use scene_decoders::{DecoderSpec, file_decoder, file_decoders, record_decoder, record_decoders};

fn assert_well_formed(specs: &[&'static DecoderSpec]) {
    let handlers: HashSet<_> = specs.iter().map(|spec| spec.handler).collect();
    assert_eq!(handlers.len(), specs.len(), "handler ids must be unique");
    for spec in specs {
        assert!(
            !spec.extensions.is_empty(),
            "{} reads no extension",
            spec.handler
        );
        for extension in spec.extensions {
            assert!(
                extension.starts_with('.') && *extension == extension.to_lowercase(),
                "{}: extension {extension:?} must be lowercase with a dot",
                spec.handler
            );
        }
    }
}

#[test]
fn handler_ids_are_unique_and_extensions_lowercase_with_a_dot() {
    let files: Vec<_> = file_decoders()
        .iter()
        .map(|decoder| decoder.spec())
        .collect();
    let records: Vec<_> = record_decoders()
        .iter()
        .map(|decoder| decoder.spec())
        .collect();
    assert_well_formed(&files);
    assert_well_formed(&records);
}

#[test]
fn lookups_find_every_built_in() {
    for decoder in file_decoders() {
        let handler = decoder.spec().handler;
        let found = file_decoder(handler).expect("every file decoder is found by its handler");
        assert_eq!(found.spec(), decoder.spec());
    }
    for decoder in record_decoders() {
        let handler = decoder.spec().handler;
        let found = record_decoder(handler).expect("every record decoder is found by its handler");
        assert_eq!(found.spec(), decoder.spec());
    }
    assert!(file_decoder("NOPE").is_none());
    assert!(record_decoder("NOPE").is_none());
}
