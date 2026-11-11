//! The network scan's routes: start or stop a scan, and follow what it finds as a stream of snapshots.
//!
//! A scan is the backend's, not a request's: it goes on when the client that started it leaves, and any number of
//! clients follow it. Only a caller who may use this machine's resources (an Owner) can start or watch one.

use super::handlers::refuse;
use crate::{AppState, access::Principal, failure, network::request_from_settings};
use api_types::live::{NetworkSnapshot, ScanRequestWire, ScanStarted};
use axum::{
    Json,
    body::{Body, Bytes},
    extract::State,
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use camera::ScanLoad;
use futures_util::stream::unfold;
use std::convert::Infallible;

/// Starts a scan of the range, ending the one that is running; its cameras start from none. Answers with the scan's
/// number, which its snapshots carry. Signing in uses the logins configured on the server, which only Full access may:
/// an Owner without it is refused when it asks to, and a scan that does not say scans without.
pub(super) async fn scan(
    State(state): State<AppState>,
    principal: Principal,
    Json(request): Json<ScanRequestWire>,
) -> Response {
    let full_access = principal.full_access();
    if !full_access && request.sign_in == Some(true) {
        return failure(
            StatusCode::FORBIDDEN,
            "sign_in",
            "Signing in with the configured logins needs full access".to_owned(),
        )
        .into_response();
    }
    match request_from_settings(
        &state.settings.state(),
        request.range.as_deref(),
        if full_access {
            request.sign_in
        } else {
            Some(false)
        },
        ScanLoad::Normal,
    ) {
        Ok(scan) => Json(ScanStarted {
            scan: state.network.rescan(scan),
        })
        .into_response(),
        Err(error) => refuse(StatusCode::UNPROCESSABLE_ENTITY, &error).into_response(),
    }
}

/// Stops the running scan, keeping what it found.
pub(super) async fn cancel(State(state): State<AppState>) -> StatusCode {
    state.network.cancel();
    StatusCode::NO_CONTENT
}

/// One JSON line.
fn line(snapshot: &NetworkSnapshot) -> Bytes {
    let mut line = serde_json::to_vec(snapshot).unwrap_or_default();
    line.push(b'\n');
    Bytes::from(line)
}

/// The scan's snapshots as newline-delimited JSON: the current one at once, then each change, which the scan makes at
/// most every 100 ms. The stream ends when the backend stops.
pub(super) async fn snapshots(State(state): State<AppState>) -> Response {
    let snapshots = state.network.subscribe();
    let stopping = state.stopping.clone();
    let lines = unfold(
        (snapshots, stopping, true),
        |(mut snapshots, mut stopping, first)| async move {
            if !first {
                tokio::select! {
                    changed = snapshots.changed() => changed.ok()?,
                    _ = stopping.wait_for(|stopping| *stopping) => return None,
                }
            }
            let next = line(&snapshots.borrow_and_update());
            Some((Ok::<_, Infallible>(next), (snapshots, stopping, false)))
        },
    );
    Response::builder()
        .header(header::CONTENT_TYPE, "application/x-ndjson")
        .header(header::CACHE_CONTROL, "no-store")
        .body(Body::from_stream(lines))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use crate::{
        network::Network,
        test_support::{TestApp, json},
    };
    use api_types::live::{NetworkSignIn, NetworkSnapshot};
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use camera::{Found, Identity, Scheme, SignIn, Web};
    use futures_util::StreamExt;
    use std::{
        net::Ipv4Addr,
        sync::{Arc, Mutex},
        time::Duration,
    };
    use tokio::{sync::mpsc, time::timeout};
    use workspace::Protocol;

    fn post(uri: &str, body: &str) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_owned()))
            .unwrap()
    }

    fn found() -> Found {
        Found {
            addr: Ipv4Addr::new(10, 9, 9, 2),
            web: Web {
                scheme: Scheme::Http,
                port: 80,
            },
            identity: Identity {
                model: "Q1656".into(),
                serial: "B8A44F000001".into(),
                firmware: "11.8.1".into(),
                product_type: "Box Camera".into(),
                architecture: String::new(),
                soc: String::new(),
            },
            uptime: Some(Duration::from_secs(90)),
            sign_in: SignIn::NotTried,
        }
    }

    #[tokio::test]
    async fn signing_in_to_found_cameras_needs_full_access() {
        let folder = tempfile::tempdir().unwrap();
        let settings =
            Arc::new(settings::Settings::load(&folder.path().join("default.json")).unwrap());
        let cache = folder.path().join("cache");
        let videos = crate::video_cache::start(&settings, &cache);
        let (_stop, stopping) = tokio::sync::watch::channel(false);
        let state = crate::AppState::new(
            Arc::clone(&settings),
            crate::access::Policy::Shared {
                password: Some(sessions::Secret::chosen("pw".into()).unwrap()),
                settings,
            },
            videos,
            stopping,
            crate::plugins::Plugins::fixed(handlers::Handlers::built_in()),
            &cache,
        )
        .with_network(crate::test_support::idle_network());
        let router = crate::app(state);
        let send = |body: &'static str| {
            let mut request = post("/api/network/scan", body);
            request
                .headers_mut()
                .insert("authorization", "Bearer pw".parse().unwrap());
            request.extensions_mut().insert(axum::extract::ConnectInfo(
                std::net::SocketAddr::from(([10, 0, 0, 9], 4000)),
            ));
            tower::ServiceExt::oneshot(router.clone(), request)
        };

        // An Owner without Full access may not use the logins the server holds, whether it asks or leaves it to the
        // setting.
        assert_eq!(
            send(r#"{"sign_in":true}"#).await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            send(r#"{"sign_in":false}"#).await.unwrap().status(),
            StatusCode::OK
        );
        assert_eq!(send("{}").await.unwrap().status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn a_scan_of_the_range_given_or_the_setting_streams_its_cameras_as_lines() {
        let sizes = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::clone(&sizes);
        let test = TestApp::scanning(Network::with_scanner(move |request| {
            seen.lock().unwrap().push(request.targets.addresses().len());
            let (found_tx, found_rx) = mpsc::channel(4);
            found_tx.try_send(Ok(found())).unwrap();
            found_rx
        }));

        let (status, body) = test
            .send(post("/api/network/scan", r#"{"range":"nope"}"#))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "range");
        let response = test
            .open(
                Request::builder()
                    .uri("/api/network")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(response.headers()["content-type"], "application/x-ndjson");
        let mut lines = response.into_body().into_data_stream();
        let mut finished = async |scan: u64| loop {
            let chunk = timeout(Duration::from_secs(10), lines.next())
                .await
                .expect("a line in time")
                .expect("the stream is open")
                .unwrap();
            let snapshot: NetworkSnapshot = serde_json::from_slice(&chunk).unwrap();
            if snapshot.scan == scan && !snapshot.scanning {
                break snapshot;
            }
        };

        let (status, body) = test.send(post("/api/network/scan", "{}")).await;
        assert_eq!(
            (status, json(&body)["scan"].as_u64()),
            (StatusCode::OK, Some(1))
        );
        finished(1).await;
        let (status, body) = test
            .send(post("/api/network/scan", r#"{"range":"10.9.9.0/30"}"#))
            .await;
        assert_eq!(
            (status, json(&body)["scan"].as_u64()),
            (StatusCode::OK, Some(2))
        );
        let last = finished(2).await;
        // The default `scan.range` is a /24 without its two ends; the range given is a /30 without them.
        assert_eq!(*sizes.lock().unwrap(), [254, 2]);
        assert_eq!(last.rows.len(), 1);
        let row = &last.rows[0];
        assert_eq!(
            (row.address.as_str(), row.scheme, row.port),
            ("10.9.9.2", Protocol::Http, 80)
        );
        assert_eq!(row.identity.model, "Q1656");
        assert_eq!(
            (row.uptime_s, &row.sign_in),
            (Some(90), &NetworkSignIn::NotTried)
        );

        assert_eq!(
            test.send(post("/api/network/cancel", "")).await.0,
            StatusCode::NO_CONTENT
        );
    }
}
