//! Live recipes through the real transport against an in-process camera: a saved environment reference expands to the
//! login its variables hold, and a literal login reaches the camera exactly as entered, spaces included.

use ax_devil::{AppState, access::Policy, app, plugins::Plugins, session::live::defaults};
use axum::{
    Router,
    body::Body,
    http::{HeaderMap, Request, StatusCode, header},
    response::{IntoResponse, Response},
    routing::any,
};
use handlers::Handlers;
use sessions::{CursorOptions, session::Command};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tower::ServiceExt;
use workspace::{
    ItemId, Protocol, Secret,
    intake::{self, NewLive, NewLiveOverlay},
};
use workspace_store::DocCommand;

const JPEG: &[u8] = &[0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10, b'J', b'F', b'I', b'F', 0];

/// The user names the camera was signed in with, in the order of the Digest answers it received.
type Users = Arc<Mutex<Vec<String>>>;

/// The `username` of a Digest answer, between its quotes.
fn digest_user(headers: &HeaderMap) -> Option<String> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let rest = value.strip_prefix("Digest ")?;
    let (_, after) = rest.split_once(r#"username=""#)?;
    Some(after.split_once('"')?.0.to_owned())
}

/// A camera that wants a Digest login for its picture. Returns its port.
async fn camera(users: Users) -> u16 {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let port = listener.local_addr().unwrap().port();
    let router = Router::new().fallback(any(move |request: Request<Body>| {
        let users = Arc::clone(&users);
        async move { answer(&users, &request) }
    }));
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    port
}

fn answer(users: &Users, request: &Request<Body>) -> Response {
    let Some(user) = digest_user(request.headers()) else {
        return (
            StatusCode::UNAUTHORIZED,
            [(
                header::WWW_AUTHENTICATE,
                r#"Digest realm="axis", nonce="abc", qop="auth", algorithm=MD5"#,
            )],
        )
            .into_response();
    };
    users.lock().unwrap().push(user);
    ([(header::CONTENT_TYPE, "image/jpeg")], JPEG).into_response()
}

/// Starts a stream of `new` in a fresh backend and returns the user the camera was signed in with.
async fn signed_in_as(new: NewLive, users: &Users) -> String {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = ax_devil::video_cache::start(&settings, &cache);
    let (_stop, stopping) = tokio::sync::watch::channel(false);
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
        .create(true, CursorOptions::default())
        .unwrap();
    let item = intake::live(
        new,
        &defaults(&state.settings.state()),
        &Handlers::built_in(),
    )
    .unwrap();
    let id: ItemId = item.id;
    let add = DocCommand::AddItems {
        items: vec![item],
        open: false,
    };
    handle.command(Command::Content(add)).unwrap();
    handle
        .command(Command::Content(DocCommand::OpenViewer(id)))
        .unwrap();
    let router = app(state.clone());
    for _ in 0..200 {
        let held = state.sessions.find_item(id);
        if held.is_some_and(|held| held.open && held.content.is_some()) {
            let request = Request::get(format!("/api/live/snapshot/{id}"))
                .header(header::HOST, "127.0.0.1")
                .body(Body::empty())
                .unwrap();
            let response = router.clone().oneshot(request).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            return users.lock().unwrap().last().cloned().unwrap_or_default();
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!("the stream did not resolve");
}

#[tokio::test]
async fn a_saved_reference_expands_to_its_login_and_a_literal_login_reaches_the_camera_as_entered()
{
    // SAFETY: this file holds one test, which sets the variables before it starts a backend or a thread of its own.
    unsafe {
        std::env::set_var("AX_DEVIL_TARGET_ADDR", "127.0.0.1");
        std::env::set_var("AX_DEVIL_TARGET_USER", " env root ");
        std::env::set_var("AX_DEVIL_TARGET_PASS", " env pw ");
    }
    let users = Users::default();
    let port = camera(Arc::clone(&users)).await;
    let stream = |host: &str, user: &str, pass: &str| NewLive {
        host: Some(host.into()),
        username: Some(user.into()),
        password: Some(Secret::new(pass)),
        protocol: Some(Protocol::Http),
        port: Some(i64::from(port)),
        overlay: Some(NewLiveOverlay::None),
        ..NewLive::default()
    };

    // The references are kept as written and expand, bound to their host, with the variables' spaces intact.
    let saved = stream(
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "$AX_DEVIL_TARGET_PASS",
    );
    assert_eq!(signed_in_as(saved, &users).await, " env root ");

    // A literal login is sent as typed.
    let literal = stream("127.0.0.1", " root ", " pw ");
    assert_eq!(signed_in_as(literal, &users).await, " root ");
}
