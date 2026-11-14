//! The backend's HTTP and WebSocket routes, one module per owning concept; each is a thin adapter over a service.
//!
//! [`ROUTES`] is the only place a route is mounted: its method, its path, the listeners that serve it and who may
//! call it. A listener mounts the rows that name it; everything else is not found there, and a method no row lists
//! for a path is answered with 405 whoever asks. `scripts/check-architecture.cjs` rejects `.route(` anywhere else.
//! What an Owner without Full access still may not see is decided by the handler from its [`Principal`](crate::access).

pub(crate) mod catalogs;
mod client_errors;
mod folder;
mod glance;
mod handlers;
mod intake;
mod launch;
mod live;
mod network;
pub mod playback;
mod plugins;
pub(crate) mod resolve;
mod scan;
mod thumbnails;
mod workspaces;

#[cfg(test)]
mod conformance;

use crate::{
    AppState,
    access::{self, Who},
    rejected,
};
use api_types::endpoints::{self, Endpoint, Method};
use axum::{
    Router,
    body::Body,
    extract::Request,
    http::{StatusCode, header},
    middleware::{Next, from_fn, from_fn_with_state},
    response::{IntoResponse, Response},
    routing::{MethodFilter, MethodRouter, on},
};
use foundation::CancelToken;
use futures_util::StreamExt;
use std::sync::Arc;

/// Cancels the blocking work of a request or socket when its future is dropped, as the server does when the client
/// goes away.
pub(crate) struct CancelOnDrop(pub(crate) CancelToken);

impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}

/// The listeners of one backend.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Listener {
    /// Loopback, for the person at this machine.
    Local,
    /// The listener for other machines that is open while a session is shared; join links only.
    Lan,
    /// A `serve` listener, where the server password is the only credential.
    Server,
}

/// One row of the table: a method on a path.
pub(crate) struct Route {
    pub method: MethodFilter,
    pub path: &'static str,
    pub listeners: &'static [Listener],
    pub who: Who,
    handler: fn(MethodFilter) -> MethodRouter<AppState>,
}

/// The axum filter of a JSON endpoint's method, so a row's path and method both come from its endpoint.
const fn filter(method: Method) -> MethodFilter {
    match method {
        Method::Get => MethodFilter::GET,
        Method::Post => MethodFilter::POST,
        Method::Patch => MethodFilter::PATCH,
    }
}

const EVERY: &[Listener] = &[Listener::Local, Listener::Lan, Listener::Server];
/// Listeners whose callers can be the person at this machine or hold the server password.
const MEMBERS: &[Listener] = &[Listener::Local, Listener::Server];
/// The desktop's own commands act on this machine, so no other listener serves them.
const LOCAL: &[Listener] = &[Listener::Local];

/// Every route the backend mounts.
pub(crate) const ROUTES: &[Route] = &[
    Route {
        method: filter(endpoints::Info::METHOD),
        path: endpoints::Info::PATH,
        listeners: EVERY,
        who: Who::Public,
        handler: |method| on(method, crate::info),
    },
    Route {
        method: filter(endpoints::ClientErrors::METHOD),
        path: endpoints::ClientErrors::PATH,
        listeners: EVERY,
        who: Who::Public,
        handler: |method| on(method, client_errors::post).layer(client_errors::limit()),
    },
    Route {
        method: filter(endpoints::GetSettings::METHOD),
        path: endpoints::GetSettings::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, crate::get_settings),
    },
    Route {
        method: filter(endpoints::PatchSettings::METHOD),
        path: endpoints::PatchSettings::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, crate::patch_settings),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/launch",
        listeners: LOCAL,
        who: Who::Owner,
        handler: |method| on(method, launch::post),
    },
    Route {
        method: filter(endpoints::GetHandlers::METHOD),
        path: endpoints::GetHandlers::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, handlers::get_handlers),
    },
    Route {
        method: filter(endpoints::GetPlugins::METHOD),
        path: endpoints::GetPlugins::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, handlers::get_plugins),
    },
    Route {
        method: filter(endpoints::InstallPlugin::METHOD),
        path: endpoints::InstallPlugin::PATH,
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::install),
    },
    Route {
        method: filter(endpoints::RemovePlugin::METHOD),
        path: endpoints::RemovePlugin::PATH,
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::remove),
    },
    Route {
        method: filter(endpoints::UpdatePlugin::METHOD),
        path: endpoints::UpdatePlugin::PATH,
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::update),
    },
    Route {
        method: filter(endpoints::CopyCatalog::METHOD),
        path: endpoints::CopyCatalog::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::copy),
    },
    Route {
        method: filter(endpoints::ReloadCatalog::METHOD),
        path: endpoints::ReloadCatalog::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::reload),
    },
    Route {
        method: filter(endpoints::DeleteCatalog::METHOD),
        path: endpoints::DeleteCatalog::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::delete),
    },
    Route {
        method: filter(endpoints::ScanResolver::METHOD),
        path: endpoints::ScanResolver::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, scan::scan),
    },
    Route {
        method: filter(endpoints::ListResolutions::METHOD),
        path: endpoints::ListResolutions::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, live::resolutions),
    },
    Route {
        method: filter(endpoints::ListSources::METHOD),
        path: endpoints::ListSources::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, live::sources),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/live/snapshot/{item}",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, glance::snapshot),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/live/snapshot/network/{address}",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, glance::found_snapshot),
    },
    Route {
        method: filter(endpoints::CheckLive::METHOD),
        path: endpoints::CheckLive::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, glance::check),
    },
    Route {
        method: filter(endpoints::ExpandLive::METHOD),
        path: endpoints::ExpandLive::PATH,
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, glance::expansions),
    },
    Route {
        method: filter(endpoints::CheckFiles::METHOD),
        path: endpoints::CheckFiles::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, intake::check),
    },
    Route {
        method: filter(endpoints::ListFolder::METHOD),
        path: endpoints::ListFolder::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, folder::list),
    },
    Route {
        method: filter(endpoints::FileExists::METHOD),
        path: endpoints::FileExists::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, folder::exists),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/network",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::snapshots),
    },
    Route {
        method: filter(endpoints::ScanNetwork::METHOD),
        path: endpoints::ScanNetwork::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::scan),
    },
    Route {
        method: filter(endpoints::CancelNetwork::METHOD),
        path: endpoints::CancelNetwork::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::cancel),
    },
    Route {
        method: filter(endpoints::ResolveResolver::METHOD),
        path: endpoints::ResolveResolver::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, resolve::resolve),
    },
    Route {
        method: filter(endpoints::RecentWorkspaces::METHOD),
        path: endpoints::RecentWorkspaces::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, workspaces::recent),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/thumbnail",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, thumbnails::get),
    },
    Route {
        method: filter(endpoints::ListSessions::METHOD),
        path: endpoints::ListSessions::PATH,
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, crate::session::list),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/session",
        listeners: EVERY,
        who: Who::Upgrade,
        handler: |method| on(method, crate::session::socket),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/playback",
        listeners: EVERY,
        who: Who::Upgrade,
        handler: |method| on(method, playback::upgrade),
    },
];

/// The sockets' paths: their failures are upgrade refusals no page reads as JSON.
const SOCKETS: [&str; 2] = ["/api/session", "/api/playback"];

/// Answers an extractor's plain-text rejection (a body that is not the route's JSON, a missing content type, a bad path
/// or query) as an `ErrorWire` under the same status, so a JSON route fails in one shape. The text is axum's own and
/// names only what the caller sent. Left as they are: responses that are not plain text (images, the scan stream,
/// bodiless 404 and 405), the sockets' upgrade refusals and any 429, which a handler words itself.
async fn wire_rejections(request: Request, next: Next) -> Response {
    let path = request.uri().path().to_owned();
    let response = next.run(request).await;
    let status = response.status();
    let plain = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|kind| kind.to_str().ok())
        .is_some_and(|kind| kind.starts_with("text/plain"));
    let rejection = (status.is_client_error() || status.is_server_error())
        && status != StatusCode::TOO_MANY_REQUESTS;
    if !plain || !rejection || !path.starts_with("/api/") || SOCKETS.contains(&path.as_str()) {
        return response;
    }
    let text = head_of(response.into_body()).await;
    let reason = if text.is_empty() {
        "the request was not accepted".to_owned()
    } else {
        text
    };
    rejected(status, reason).into_response()
}

/// How many bytes of a rejection's text an `ErrorWire` carries: serde's message can quote a whole value the caller sent.
const REASON_MOST: usize = 1024;

/// The start of `body` as text, at most [`REASON_MOST`] bytes of it, with an ellipsis when more followed. Only the
/// bytes kept are copied and nothing is read once the budget is spent and one more byte has shown there is more; a
/// character cut in two is left out whole.
async fn head_of(body: Body) -> String {
    let mut stream = body.into_data_stream();
    let mut read = Vec::new();
    let mut cut = false;
    while let Some(Ok(chunk)) = stream.next().await {
        let room = REASON_MOST - read.len();
        read.extend_from_slice(&chunk[..chunk.len().min(room)]);
        if chunk.len() > room {
            cut = true;
            break;
        }
    }
    let mut text = match std::str::from_utf8(&read) {
        Ok(text) => text.to_owned(),
        Err(error) if cut && error.error_len().is_none() => {
            String::from_utf8_lossy(&read[..error.valid_up_to()]).into_owned()
        }
        Err(_) => String::from_utf8_lossy(&read).into_owned(),
    };
    if cut {
        text.push('\u{2026}');
    }
    text
}

/// The routes `listener` serves, and in release builds the embedded frontend for every other GET.
pub(crate) fn mount(listener: Listener, state: AppState) -> Router {
    let mut router = Router::new();
    for route in ROUTES
        .iter()
        .filter(|route| route.listeners.contains(&listener))
    {
        // On the method router, so a method no row lists is 405 before anyone's credentials are looked at.
        let gate = from_fn_with_state((Arc::clone(&state.policy), route.who), access::require);
        router = router.route(route.path, (route.handler)(route.method).route_layer(gate));
    }
    #[cfg(feature = "embed-assets")]
    {
        router = router.fallback_service(axum::routing::get(crate::asset));
    }
    router.with_state(state).layer(from_fn(wire_rejections))
}

#[cfg(test)]
mod tests {
    use super::{Body, REASON_MOST, head_of};
    use futures_util::stream;
    use std::convert::Infallible;

    async fn head(chunks: &[&[u8]]) -> String {
        let chunks: Vec<Result<Vec<u8>, Infallible>> =
            chunks.iter().map(|chunk| Ok(chunk.to_vec())).collect();
        head_of(Body::from_stream(stream::iter(chunks))).await
    }

    #[tokio::test]
    async fn a_rejection_keeps_its_first_bytes_and_an_ellipsis_only_when_more_followed() {
        let full = "a".repeat(REASON_MOST);
        let (head_part, rest) = full.as_bytes().split_at(1000);
        // Exactly the budget, in one chunk or several, even with an empty one after: nothing was lost.
        assert_eq!(head(&[full.as_bytes()]).await, full);
        assert_eq!(head(&[head_part, rest, b""]).await, full);
        // One byte more, wherever the chunk boundary falls, or a far longer one: cut, and no more is kept.
        let cut = format!("{full}\u{2026}");
        assert_eq!(head(&[full.as_bytes(), b"b"]).await, cut);
        assert_eq!(
            head(&[head_part, b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]).await,
            cut
        );
        assert_eq!(head(&["a".repeat(5000).as_bytes()]).await, cut);
        // A character straddling the cutoff is dropped whole, not left as a replacement.
        let straddling = format!("{}\u{e9}", "a".repeat(REASON_MOST - 1));
        assert_eq!(
            head(&[straddling.as_bytes()]).await,
            format!("{}\u{2026}", "a".repeat(REASON_MOST - 1))
        );
        // Nothing to read is nothing.
        assert_eq!(head(&[]).await, "");
    }
}
