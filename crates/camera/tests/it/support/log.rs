//! The app's logging in miniature, for a test that needs retina's `log` records: the `log` bridge with the camera
//! crate's layer, next to a layer that keeps only errors, as `--log-level error` does.

use std::sync::Once;

use tracing_subscriber::Layer;
use tracing_subscriber::filter::LevelFilter;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

/// Installs the global subscriber once per test binary.
pub fn install() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        tracing_subscriber::registry()
            .with(camera::retina_log_layer())
            .with(
                tracing_subscriber::fmt::layer()
                    .with_writer(std::io::sink)
                    .with_filter(LevelFilter::ERROR),
            )
            .init();
    });
}
