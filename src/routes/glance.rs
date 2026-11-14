//! The sidebar's questions about a camera: a picture for a hover card, whether the login saved with a stream gets in,
//! and what the references of the Add Live Stream form show. They are adapters over [`live::glance`] and the
//! reference rules of the `workspace` crate.
//!
//! A stream's own login goes only to a host the user started: a viewer was opened for it. Anything else is answered
//! as unknown without a request, so opening a workspace file sends nothing. A found camera's picture uses the scan login the scan
//! already signed in with. Nothing here returns or logs a login.

use crate::{AppState, session::live::defaults};
use api_types::live::{ExpansionsRequest, LiveCheckRequest, LiveExpansions, NetworkSignIn};
use axum::{
    Json,
    extract::{Path, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use bytes::Bytes;
use live::{Camera, Head, Host, Scheme, Web, camera_of, glance::SNAPSHOT_TIMEOUT};
use sessions::session::HeldItem;
use std::net::Ipv4Addr;
use workspace::{
    Connection, Content, Environment, HostReading, ItemId, LiveVideoContent, Protocol,
    ResolvedCamera,
};

/// The live stream `id` names and what it resolved to, if it is a resolved live stream.
fn live_item(state: &AppState, id: &str) -> Option<(HeldItem, LiveVideoContent)> {
    let held = state.sessions.find_item(ItemId::parse(id)?)?;
    match held.content.clone()? {
        Content::LiveVideo(content) => Some((held, *content)),
        Content::SeekableVideo(_) | Content::Playlist(_) => None,
    }
}

/// Whether the user started the stream's host (its viewer is `open`, or one was opened for the host), so its own login
/// may be sent there.
fn started(state: &AppState, open: bool, camera: &ResolvedCamera) -> bool {
    open || state.live.has_started(&camera.host)
}

/// A JPEG, uncached, or a 404 when there is none.
pub(super) fn jpeg(picture: Option<Bytes>) -> Response {
    match picture {
        Some(picture) => (
            [
                (header::CONTENT_TYPE, "image/jpeg"),
                (header::CACHE_CONTROL, "no-store"),
            ],
            picture,
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// The picture of the stream `item` names, 404 when it is not a resolved live stream or has none.
pub(super) async fn snapshot(State(state): State<AppState>, Path(item): Path<String>) -> Response {
    let Some((held, content)) = live_item(&state, &item) else {
        return jpeg(None);
    };
    jpeg(
        state
            .glances
            .stream_picture(&state.live, &content, held.open)
            .await,
    )
}

/// A snapshot of the first head of a camera the scan found, with the scan login that signed in to it.
pub(super) async fn found_snapshot(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Response {
    let scan = state.network.snapshot();
    let found = scan.rows.iter().find(|row| row.address == address);
    let Some((row, NetworkSignIn::SignedIn { login_index, .. })) =
        found.map(|row| (row, &row.sign_in))
    else {
        return jpeg(None);
    };
    // The login this scan tried, not whatever the settings hold now at that position.
    let login = state.network.accepted_login(scan.scan, *login_index);
    let Ok(host) = Host::parse(&address) else {
        return jpeg(None);
    };
    let mut camera = Camera::new(host);
    camera.timeout = SNAPSHOT_TIMEOUT;
    camera.web = Web {
        scheme: match row.scheme {
            Protocol::Http => Scheme::Http,
            Protocol::Https => Scheme::Https,
        },
        port: row.port,
    };
    camera.login = login;
    if camera.login.is_none() {
        return jpeg(None);
    }
    jpeg(state.glances.snapshot(camera, Head::FIRST).await)
}

/// What signing in with the stream's own login finds, as a scan row; 204 when nothing is known (see the module).
pub(super) async fn check(
    State(state): State<AppState>,
    Json(request): Json<LiveCheckRequest>,
) -> Response {
    let unknown = || StatusCode::NO_CONTENT.into_response();
    // Signing in to the workspace's cameras is the user's to turn off with the scan's own sign-in setting.
    let signs_in = state
        .settings
        .state()
        .stored("scan.sign_in")
        .and_then(|value| value.as_bool())
        .unwrap_or(true);
    if !signs_in {
        return unknown();
    }
    let Some((held, content)) = live_item(&state, &request.item) else {
        return unknown();
    };
    let resolved = &content.camera;
    let Ok(addr) = resolved.host.parse::<Ipv4Addr>() else {
        return unknown();
    };
    let camera = camera_of(resolved, SNAPSHOT_TIMEOUT).ok();
    let Some((web, login)) = camera.and_then(|camera| {
        let login = camera.login.filter(|login| !login.user.is_empty())?;
        Some((camera.web, login))
    }) else {
        return unknown();
    };
    if !started(&state, held.open, resolved) {
        return unknown();
    }
    match state
        .glances
        .check(&format!("item/{}", request.item), addr, web, login)
        .await
    {
        Some(row) => Json(row).into_response(),
        None => unknown(),
    }
}

/// What the references of the form's default connection texts show, for the host the form has now: never a password,
/// only whether it is set.
pub(super) async fn expansions(
    State(state): State<AppState>,
    Json(request): Json<ExpansionsRequest>,
) -> Json<LiveExpansions> {
    let settings = state.settings.state();
    let defaults = defaults(&settings, true);
    let typed = |text: Option<String>, default: &str| {
        text.map(|text| text.trim().to_owned())
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| default.to_owned())
    };
    let host = typed(request.host, &defaults.host);
    let broker_host = typed(request.broker_host, &defaults.mqtt.host);
    // The form shows a host that is unset or was rejected as nothing; which of the two is for the command line to say.
    let text = |reading: HostReading| match reading {
        HostReading::Host(host) => host,
        HostReading::Unset | HostReading::Rejected => String::new(),
    };
    let shown = workspace::preview(
        &Environment::of_process(),
        Connection {
            host: &host,
            user: &defaults.username,
            password: &defaults.password,
        },
        Some(Connection {
            host: &broker_host,
            user: &defaults.mqtt.user,
            password: &defaults.mqtt.password,
        }),
    );
    Json(LiveExpansions {
        host: shown.camera.host.map(text),
        username: shown.camera.user,
        password_set: shown.camera.password_set,
        broker_host: shown.broker.host.map(text),
        broker_user: shown.broker.user,
        broker_pass_set: shown.broker.password_set,
    })
}
