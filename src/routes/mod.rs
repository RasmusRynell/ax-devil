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
pub mod window;
mod workspaces;

#[cfg(test)]
mod conformance;

use crate::{
    AppState,
    access::{self, Who},
};
use axum::{
    Router,
    middleware::from_fn_with_state,
    routing::{MethodFilter, MethodRouter, on},
};
use foundation::CancelToken;
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

const EVERY: &[Listener] = &[Listener::Local, Listener::Lan, Listener::Server];
/// Listeners whose callers can be the person at this machine or hold the server password.
const MEMBERS: &[Listener] = &[Listener::Local, Listener::Server];
/// The desktop's own files: window positions and installed plugins belong to this machine, so no other listener
/// serves them.
const LOCAL: &[Listener] = &[Listener::Local];

/// Every route the backend mounts.
pub(crate) const ROUTES: &[Route] = &[
    Route {
        method: MethodFilter::GET,
        path: "/api/info",
        listeners: EVERY,
        who: Who::Public,
        handler: |method| on(method, crate::info),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/client-errors",
        listeners: EVERY,
        who: Who::Public,
        handler: |method| on(method, client_errors::post).layer(client_errors::limit()),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/settings",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, crate::get_settings),
    },
    Route {
        method: MethodFilter::PATCH,
        path: "/api/settings",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, crate::patch_settings),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/windows/{name}",
        listeners: LOCAL,
        who: Who::Owner,
        handler: |method| on(method, window::get),
    },
    Route {
        method: MethodFilter::PUT,
        path: "/api/windows/{name}",
        listeners: LOCAL,
        who: Who::Owner,
        handler: |method| on(method, window::put),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/launch",
        listeners: LOCAL,
        who: Who::Owner,
        handler: |method| on(method, launch::post),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/handlers",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, handlers::get_handlers),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/plugins",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, handlers::get_plugins),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/install",
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::install),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/remove",
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::remove),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/update",
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, plugins::update),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/catalogs/copy",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::copy),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/catalogs/reload",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::reload),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/catalogs/delete",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, catalogs::delete),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/resolvers/{id}/scan",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, scan::scan),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/live/resolutions",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, live::resolutions),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/live/sources",
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
        method: MethodFilter::POST,
        path: "/api/live/check",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, glance::check),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/live/expansions",
        listeners: MEMBERS,
        who: Who::FullAccess,
        handler: |method| on(method, glance::expansions),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/files/check",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, intake::check),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/files/folder",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, folder::list),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/network",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::snapshots),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/network/scan",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::scan),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/network/cancel",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, network::cancel),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/resolvers/{id}/resolve",
        listeners: MEMBERS,
        who: Who::Owner,
        handler: |method| on(method, resolve::resolve),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/workspaces/recent",
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
        method: MethodFilter::GET,
        path: "/api/sessions",
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
    router.with_state(state)
}
