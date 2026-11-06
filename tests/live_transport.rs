//! Live recipes through the real transport against an in-process camera: a saved environment reference expands to the
//! login its variables hold, and a literal login reaches the camera exactly as entered, spaces included.

use ax_devil::{AppState, access::Policy, app, plugins::Plugins, session::live::defaults};
use axum::{
    Router,
    body::Body,
    http::{HeaderMap, Request, StatusCode, header},
    response::IntoResponse,
    routing::any,
};
use handlers::Handlers;
use sessions::{CursorOptions, session::Command};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tower::ServiceExt;
use workspace::{
    ItemId, Protocol, Secret,
    intake::{self, NewLive, NewLiveOverlay},
};
use workspace_store::DocCommand;

const JPEG: &[u8] = &[0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10, b'J', b'F', b'I', b'F', 0];

/// MD5 (RFC 1321), as the Digest login of the camera needs it; the test crates have no hash crate to use.
fn md5(data: &[u8]) -> [u8; 16] {
    const SHIFT: [u32; 16] = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
    let mut message = data.to_vec();
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend(((data.len() as u64) * 8).to_le_bytes());
    let mut state: [u32; 4] = [0x6745_2301, 0xefcd_ab89, 0x98ba_dcfe, 0x1032_5476];
    for block in message.chunks(64) {
        let words: Vec<u32> = block
            .chunks(4)
            .map(|word| u32::from_le_bytes(word.try_into().unwrap()))
            .collect();
        let [mut a, mut b, mut c, mut d] = state;
        for i in 0..64 {
            let (mix, word) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let constant = ((i as f64 + 1.0).sin().abs() * 4_294_967_296.0) as u32;
            let sum = a
                .wrapping_add(mix)
                .wrapping_add(constant)
                .wrapping_add(words[word]);
            (a, d, c) = (d, c, b);
            b = b.wrapping_add(sum.rotate_left(SHIFT[(i / 16) * 4 + i % 4]));
        }
        for (word, add) in state.iter_mut().zip([a, b, c, d]) {
            *word = word.wrapping_add(add);
        }
    }
    let mut digest = [0; 16];
    for (bytes, word) in digest.chunks_mut(4).zip(state) {
        bytes.copy_from_slice(&word.to_le_bytes());
    }
    digest
}

fn md5_hex(text: &str) -> String {
    md5(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The parameters of a `Digest` answer, by name.
fn digest_params(headers: &HeaderMap) -> Option<HashMap<String, String>> {
    let rest = headers
        .get(header::AUTHORIZATION)?
        .to_str()
        .ok()?
        .strip_prefix("Digest ")?;
    let (mut params, mut name, mut text, mut quoted) =
        (HashMap::new(), String::new(), String::new(), false);
    let mut value = false;
    for c in rest.chars().chain([',']) {
        match c {
            '"' => quoted = !quoted,
            ',' if !quoted => {
                params.insert(name.trim().to_owned(), text.clone());
                name.clear();
                text.clear();
                value = false;
            }
            '=' if !value && !quoted => value = true,
            _ if value => text.push(c),
            _ => name.push(c),
        }
    }
    Some(params)
}

/// Whether the Digest answer is the one for `user` and `password` that this camera's challenge asks.
fn signed_by(headers: &HeaderMap, user: &str, password: &str) -> bool {
    let Some(p) = digest_params(headers) else {
        return false;
    };
    let get = |name: &str| p.get(name).map_or("", String::as_str);
    let ha1 = md5_hex(&format!("{user}:axis:{password}"));
    let ha2 = md5_hex(&format!("GET:{}", get("uri")));
    let response = md5_hex(&format!(
        "{ha1}:abc:{}:{}:auth:{ha2}",
        get("nc"),
        get("cnonce")
    ));
    get("username") == user && get("response") == response
}

/// A camera that wants a Digest login for its picture, and counts the requests signed with exactly `user` and
/// `password`. Returns its port.
async fn camera(user: &'static str, password: &'static str, signed: Arc<AtomicUsize>) -> u16 {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let port = listener.local_addr().unwrap().port();
    let router = Router::new().fallback(any(move |request: Request<Body>| {
        let signed = Arc::clone(&signed);
        async move {
            if signed_by(request.headers(), user, password) {
                signed.fetch_add(1, Ordering::SeqCst);
                return ([(header::CONTENT_TYPE, "image/jpeg")], JPEG).into_response();
            }
            (
                StatusCode::UNAUTHORIZED,
                [(
                    header::WWW_AUTHENTICATE,
                    r#"Digest realm="axis", nonce="abc", qop="auth", algorithm=MD5"#,
                )],
            )
                .into_response()
        }
    }));
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    port
}

/// Whether a camera that takes only `user` and `password` accepts the picture request of the stream that `new` makes
/// for its port, once started in a fresh backend.
async fn camera_accepts(
    new: impl FnOnce(u16) -> NewLive,
    user: &'static str,
    password: &'static str,
) -> bool {
    let signed = Arc::new(AtomicUsize::new(0));
    let port = camera(user, password, Arc::clone(&signed)).await;
    let new = new(port);
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
        .create(true, true, CursorOptions::default())
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
            return response.status() == StatusCode::OK && signed.load(Ordering::SeqCst) > 0;
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
    let stream = |host: &'static str, user: &'static str, pass: &'static str| {
        move |port: u16| NewLive {
            host: Some(host.into()),
            username: Some(user.into()),
            password: Some(Secret::new(pass)),
            protocol: Some(Protocol::Http),
            port: Some(i64::from(port)),
            overlay: Some(NewLiveOverlay::None),
            ..NewLive::default()
        }
    };

    // The references are kept as written and expand, bound to their host, with the variables' spaces intact.
    let saved = stream(
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "$AX_DEVIL_TARGET_PASS",
    );
    assert!(camera_accepts(saved, " env root ", " env pw ").await);

    // A literal login is sent as typed; the camera takes no other user or password.
    assert!(camera_accepts(stream("127.0.0.1", " root ", " pw "), " root ", " pw ").await);
    assert!(
        !camera_accepts(stream("127.0.0.1", " root ", " pw "), "root", "pw").await,
        "a camera that wants the trimmed login does not accept the entered one"
    );
}
