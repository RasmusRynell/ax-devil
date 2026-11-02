//! Built-in Scene decoders: overlay files and records turned into Scenes (MOT, UVG-VCM, ONVIF XML, CVAT, ADF).
//!
//! They implement [`handler_api::Decoder`]; these are not the playback crate's video decoders. The registry that
//! finds them by id or file name is `handlers`.

mod adf;
mod cvat;
mod iso8601;
mod mot;
mod onvif_xml;
mod uvg_vcm;

use std::sync::Arc;

use handler_api::Decoder;

/// Every built-in decoder.
pub fn decoders() -> Vec<Arc<dyn Decoder>> {
    let mut decoders: Vec<Arc<dyn Decoder>> = vec![
        Arc::new(mot::MotDecoder::new()),
        Arc::new(uvg_vcm::UvgVcmDecoder::new()),
        Arc::new(onvif_xml::OnvifXmlDecoder::new()),
        Arc::new(cvat::CvatDecoder::new()),
    ];
    decoders.extend(adf::decoders());
    decoders
}
