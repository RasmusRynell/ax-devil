//! The sidebar's questions about a camera: a picture for a hover card, whether the login saved with a stream gets in,
//! and what the references of the Add Live Stream form show. They are adapters over [`crate::glance`] and the
//! reference rules of the `workspace` crate.
//!
//! A stream's own login goes only to a host the user vouched for: a viewer was opened for it, its host is the
//! reference bound to the camera host, or a reference was confirmed for it. Anything else is answered as unknown
//! without a request, so opening a workspace file sends nothing. A found camera's picture uses the scan login the scan
//! already signed in with. Nothing here returns or logs a login.

use crate::{
    AppState,
    glance::SNAPSHOT_TIMEOUT,
    live_sources::camera_of,
    session::live::{defaults, environment},
};
use api_types::live::{
    ExpansionsRequest, LiveCheckRequest, LiveExpansions, LiveProtocol, NetworkSignIn,
};
use axum::{
    Json,
    extract::{Path, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use bytes::Bytes;
use camera::{Camera, Head, Host, Scheme, Web};
use sessions::session::HeldItem;
use std::net::Ipv4Addr;
use workspace::{Connection, Content, ItemId, LiveVideoContent, ResolvedCamera, Source};

/// The live stream `id` names and what it resolved to, if it is a resolved live stream.
fn live_item(state: &AppState, id: &str) -> Option<(HeldItem, LiveVideoContent)> {
    let held = state.sessions.find_item(ItemId::parse(id)?)?;
    match held.content.clone()? {
        Content::LiveVideo(content) => Some((held, *content)),
        Content::SeekableVideo(_) | Content::Playlist(_) => None,
    }
}

/// Whether the user vouched for the stream's host, so its own login may be sent there.
fn vouched(state: &AppState, held: &HeldItem, camera: &ResolvedCamera) -> bool {
    held.open
        || state.live.has_started(&camera.host)
        || matches!(&held.item.recipe.source, Source::Live(recipe)
            if workspace::vouches(&environment(&state.settings.state()), &recipe.host, &camera.host))
}

fn jpeg(picture: Option<Bytes>) -> Response {
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

/// A snapshot of the stream's camera, with the stream's own login: 404 when the stream is not a resolved live stream,
/// the host is not vouched for, or the camera does not give one. At most one request to the camera per 10 s.
pub(super) async fn snapshot(State(state): State<AppState>, Path(item): Path<String>) -> Response {
    let Some((held, content)) = live_item(&state, &item) else {
        return jpeg(None);
    };
    let resolved = &content.camera;
    let (Some(head), Ok(camera)) = (
        Head::new(resolved.head),
        camera_of(resolved, SNAPSHOT_TIMEOUT),
    ) else {
        return jpeg(None);
    };
    if !vouched(&state, &held, resolved) {
        return jpeg(None);
    }
    jpeg(
        state
            .glances
            .snapshot(&format!("item/{item}"), camera, head)
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
            LiveProtocol::Http => Scheme::Http,
            LiveProtocol::Https => Scheme::Https,
        },
        port: row.port,
    };
    camera.login = login;
    if camera.login.is_none() {
        return jpeg(None);
    }
    jpeg(
        state
            .glances
            .snapshot(
                &format!("network/{address}/{}/{login_index}", scan.scan),
                camera,
                Head::FIRST,
            )
            .await,
    )
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
    if !vouched(&state, &held, resolved) {
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

/// What the references of the form's default connection texts show, for the host the form has now. Only for the
/// references the binding rule lets expand, and never a password.
pub(super) async fn expansions(
    State(state): State<AppState>,
    Json(request): Json<ExpansionsRequest>,
) -> Json<LiveExpansions> {
    let settings = state.settings.state();
    let defaults = defaults(&settings);
    let typed = |text: Option<String>, default: &str| {
        text.map(|text| text.trim().to_owned())
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| default.to_owned())
    };
    let host = typed(request.host, &defaults.host);
    let broker_host = typed(request.broker_host, &defaults.mqtt.host);
    let shown = workspace::preview(
        &environment(&settings),
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
        host: shown.camera.host,
        username: shown.camera.user,
        password_set: shown.camera.password_set,
        broker_host: shown.broker.host,
        broker_user: shown.broker.user,
        broker_pass_set: shown.broker.password_set,
    })
}
