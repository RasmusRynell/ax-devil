//! The sidebar's questions about a stream's camera against an in-process camera: its own login goes only to a host the
//! user started, only as a Digest answer, and a hover or a poll asks the camera once per cooldown.

use ax_devil::{AppState, access::Policy, app, plugins::Plugins, session::live::defaults};
use axum::{
    Router,
    body::{Body, Bytes, to_bytes},
    http::{HeaderMap, Request, StatusCode, header},
    response::{IntoResponse, Response},
    routing::any,
};
use handlers::Handlers;
use serde_json::{Value, json};
use sessions::{CursorOptions, session::Command};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tower::ServiceExt;
use workspace::store::DocCommand;
use workspace::{
    ItemId, Protocol, Secret,
    intake::{self, NewLive, NewLiveOverlay},
};

const JPEG: &[u8] = &[0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10, b'J', b'F', b'I', b'F', 0];

/// What the camera was asked, as `(path, whether the request carried a Digest answer for root)`.
type Seen = Arc<Mutex<Vec<(String, bool)>>>;

/// An Axis camera that wants a Digest login for everything but who-are-you. Returns its port.
async fn camera(seen: Seen) -> u16 {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let port = listener.local_addr().unwrap().port();
    let router = Router::new().fallback(any(move |request: Request<Body>| {
        let seen = Arc::clone(&seen);
        async move { answer(&seen, request).await }
    }));
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    port
}

fn digest_for_root(headers: &HeaderMap) -> bool {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("Digest ") && value.contains(r#"username="root""#))
}

async fn answer(seen: &Seen, request: Request<Body>) -> Response {
    let path = request.uri().path().to_owned();
    let signed = digest_for_root(request.headers());
    seen.lock().unwrap().push((path.clone(), signed));
    let body: Bytes = to_bytes(request.into_body(), 1 << 16).await.unwrap();
    let asks_who = String::from_utf8_lossy(&body).contains("getAllUnrestrictedProperties");
    if path.ends_with("basicdeviceinfo.cgi") && asks_who {
        let info = json!({"data": {"propertyList": {
            "Brand": "AXIS", "ProdShortName": "Q1656", "SerialNumber": "B8A44F000001", "Version": "11.8.1",
        }}});
        return axum::Json(info).into_response();
    }
    if !signed {
        return (
            StatusCode::UNAUTHORIZED,
            [(
                header::WWW_AUTHENTICATE,
                r#"Digest realm="axis", nonce="abc", qop="auth", algorithm=MD5"#,
            )],
        )
            .into_response();
    }
    if path.ends_with("image.cgi") {
        return ([(header::CONTENT_TYPE, "image/jpeg")], JPEG).into_response();
    }
    axum::Json(json!({"data": {"propertyList": {
        "Brand": "AXIS", "ProdShortName": "Q1656", "Architecture": "aarch64", "Soc": "CV25",
    }}}))
    .into_response()
}

struct World {
    router: Router,
    state: AppState,
    _folder: tempfile::TempDir,
    _stop: tokio::sync::watch::Sender<bool>,
    handle: sessions::session::Handle,
}

impl World {
    async fn new() -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let cache = folder.path().join("cache");
        let videos = ax_devil::video_cache::start(&settings, &cache);
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let state = AppState::new(
            Arc::new(settings),
            Policy::Local,
            videos,
            stopping,
            Plugins::fixed(Handlers::built_in()),
            &cache,
        );
        let handle = state
            .sessions
            .create(true, CursorOptions::default(), Default::default())
            .unwrap();
        Self {
            router: app(state.clone()),
            state,
            _folder: folder,
            _stop: stop,
            handle,
        }
    }

    /// Adds a stream of the camera on `port` with the login root, not started, and waits until it has resolved.
    async fn add_stream(&self, port: u16) -> ItemId {
        let defaults = defaults(&self.state.settings.state(), true);
        let new = NewLive {
            host: Some("127.0.0.1".into()),
            username: Some("root".into()),
            password: Some(Secret::new("pw")),
            protocol: Some(Protocol::Http),
            port: Some(i64::from(port)),
            overlay: Some(NewLiveOverlay::None),
            ..NewLive::default()
        };
        let item = intake::live(new, &defaults, &Handlers::built_in()).unwrap();
        let id = item.id;
        let add = DocCommand::AddItems {
            items: vec![item],
            open: false,
        };
        self.handle.command(Command::Content(add)).unwrap();
        for _ in 0..200 {
            if self
                .state
                .sessions
                .find_item(id)
                .is_some_and(|held| held.content.is_some())
            {
                return id;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("the stream did not resolve");
    }

    /// Opens the viewer of `item` and waits until it is open.
    async fn start(&self, item: ItemId) {
        self.handle
            .command(Command::Content(DocCommand::OpenViewer(item)))
            .unwrap();
        for _ in 0..200 {
            if self
                .state
                .sessions
                .find_item(item)
                .is_some_and(|held| held.open && held.content.is_some())
            {
                return;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("the viewer did not open");
    }

    async fn call(&self, request: Request<Body>) -> (StatusCode, Vec<u8>) {
        let mut request = request;
        request
            .headers_mut()
            .insert(header::HOST, "127.0.0.1".parse().unwrap());
        let response = self.router.clone().oneshot(request).await.unwrap();
        let status = response.status();
        (
            status,
            to_bytes(response.into_body(), 1 << 20)
                .await
                .unwrap()
                .to_vec(),
        )
    }

    async fn snapshot(&self, item: ItemId) -> (StatusCode, Vec<u8>) {
        let request = Request::get(format!("/api/live/snapshot/{item}")).body(Body::empty());
        self.call(request.unwrap()).await
    }

    async fn check(&self, item: ItemId) -> (StatusCode, Vec<u8>) {
        let request = Request::post("/api/live/check")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(json!({ "item": item.to_string() }).to_string()));
        self.call(request.unwrap()).await
    }
}

#[tokio::test]
async fn a_stream_the_user_has_not_started_is_sent_nothing_and_a_started_one_is_asked_once_per_cooldown()
 {
    let seen = Seen::default();
    let port = camera(Arc::clone(&seen)).await;
    let world = World::new().await;
    let item = world.add_stream(port).await;

    // Resolved from a file or just added without Start: the host is not started.
    assert_eq!(world.snapshot(item).await.0, StatusCode::NOT_FOUND);
    assert_eq!(world.check(item).await.0, StatusCode::NO_CONTENT);
    assert!(seen.lock().unwrap().is_empty(), "nothing was sent");

    // Started: the picture comes with the stream's own login, as a Digest answer.
    world.start(item).await;
    let (status, picture) = world.snapshot(item).await;
    assert_eq!((status, picture.as_slice()), (StatusCode::OK, JPEG));
    let asked = seen.lock().unwrap().len();
    assert_eq!(asked, 2, "the challenge, then the signed request");
    assert!(seen.lock().unwrap()[1].1);
    assert_eq!(world.snapshot(item).await.0, StatusCode::OK);
    assert_eq!(seen.lock().unwrap().len(), asked, "the cooldown answered");

    // Another stream of the same camera, login and head is the same read.
    let other = world.add_stream(port).await;
    world.start(other).await;
    assert_eq!(world.snapshot(other).await.0, StatusCode::OK);
    assert_eq!(seen.lock().unwrap().len(), asked, "one cooldown for both");

    // The Workspace view asks by the session and the item's content id, and gets the same picture.
    let request = Request::get(format!(
        "/api/thumbnail?session={}&entry={item}",
        world.handle.session()
    ))
    .body(Body::empty());
    let (status, picture) = world.call(request.unwrap()).await;
    assert_eq!((status, picture.as_slice()), (StatusCode::OK, JPEG));
    assert_eq!(seen.lock().unwrap().len(), asked, "the same cooldown");

    // The check tells what signing in with that login found, as a scan row.
    let (status, row) = world.check(item).await;
    assert_eq!(status, StatusCode::OK);
    let row: Value = serde_json::from_slice(&row).unwrap();
    assert_eq!(row["identity"]["model"], "Q1656");
    assert_eq!(row["sign_in"]["state"], "SignedIn");
    assert!(!String::from_utf8_lossy(&serde_json::to_vec(&row).unwrap()).contains("pw"));
}
