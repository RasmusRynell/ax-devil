//! Desktop smoke fixture: the transport tests' RTSP peer with real pictures and loopback-only controls.
#[path = "../tests/it/support/mod.rs"]
mod support;

use axum::{Router, http::header, routing::get};
use std::{
    io::Write,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use support::fake_rtsp::{Behaviour, FakeRtsp};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let peer = Arc::new(
        FakeRtsp::with(Behaviour {
            login: false,
            pictures: true,
            interval: Duration::from_millis(33),
            ..Behaviour::default()
        })
        .await,
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let port = listener.local_addr()?.port();
    writeln!(std::io::stdout(), "{} {port}", peer.addr.port())?;
    let play = Arc::clone(&peer);
    let drop = Arc::clone(&peer);
    let codec = Arc::clone(&peer);
    let snapshots = Arc::new(AtomicUsize::new(0));
    let reads = Arc::clone(&snapshots);
    let app = Router::new()
        .route(
            "/snapshots",
            get(move || async move { reads.load(Ordering::SeqCst).to_string() }),
        )
        .route(
            "/axis-cgi/jpg/image.cgi",
            get(move || {
                snapshots.fetch_add(1, Ordering::SeqCst);
                async {
                    (
                        [(header::CONTENT_TYPE, "image/jpeg")],
                        include_bytes!("../../../fixtures/video/rtsp-snapshot.jpg").as_slice(),
                    )
                }
            }),
        )
        .route(
            "/play",
            get(move || async move {
                play.pause(false);
            }),
        )
        .route(
            "/drop",
            get(move || async move {
                drop.pause(true);
                drop.next_picture();
                drop.disconnect();
            }),
        )
        .route(
            "/codec",
            get(move || async move {
                codec.unsupported();
                codec.disconnect();
            }),
        );
    axum::serve(listener, app).await?;
    Ok(())
}
