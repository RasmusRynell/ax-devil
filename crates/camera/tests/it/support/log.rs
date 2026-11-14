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

/// What the camera crate itself logs while a test runs, for the thread of a current-thread runtime.
#[derive(Clone, Default)]
pub struct Captured(std::sync::Arc<std::sync::Mutex<Vec<String>>>);

impl Captured {
    /// Every event so far, as text: its target, level and fields.
    pub fn text(&self) -> String {
        self.0.lock().expect("lock").join("\n")
    }
}

struct Recorder(Captured);

#[derive(Default)]
struct Fields(String);

impl tracing::field::Visit for Fields {
    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        self.0 += &format!(" {}={value:?}", field.name());
    }
}

impl<S: tracing::Subscriber> Layer<S> for Recorder {
    fn on_event(&self, event: &tracing::Event<'_>, _: tracing_subscriber::layer::Context<'_, S>) {
        // A record that came through the `log` bridge is named by the target it was logged with. Retina's are not
        // kept: the app's logging never writes them (`logging`'s tests), and this keeps what the camera crate says.
        let normalized = tracing_log::NormalizeEvent::normalized_metadata(event);
        let meta = normalized.as_ref().unwrap_or_else(|| event.metadata());
        if meta.target().starts_with("retina") {
            return;
        }
        let mut fields = Fields::default();
        event.record(&mut fields);
        self.0.0.lock().expect("lock").push(format!(
            "{} {}{}",
            meta.level(),
            meta.target(),
            fields.0
        ));
    }
}

/// Starts keeping what the thread logs until the guard is dropped.
pub fn capture() -> (tracing::subscriber::DefaultGuard, Captured) {
    let captured = Captured::default();
    let subscriber = tracing_subscriber::registry().with(Recorder(captured.clone()));
    (tracing::subscriber::set_default(subscriber), captured)
}
