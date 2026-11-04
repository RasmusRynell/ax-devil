//! The backend's HTTP and WebSocket routes, one module per owning concept; each is a thin adapter over a service.
//!
//! [`ROUTES`] is the only place a route is mounted: its method, its path, the listeners that serve it and who may
//! call it. A listener mounts the rows that name it; everything else is not found there, and a method no row lists
//! for a path is answered with 405 whoever asks. `scripts/check-architecture.cjs` rejects `.route(` anywhere else.
//! Which setting rows or sessions a caller may see is decided by the handler from its [`Principal`](crate::access).

mod catalogs;
mod client_errors;
mod handlers;
pub mod playback;
mod plugins;
pub(crate) mod resolve;
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
        who: Who::Settings,
        handler: |method| on(method, crate::get_settings),
    },
    Route {
        method: MethodFilter::PUT,
        path: "/api/settings",
        listeners: MEMBERS,
        who: Who::Settings,
        handler: |method| on(method, crate::put_settings),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/windows/{name}",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, window::get),
    },
    Route {
        method: MethodFilter::PUT,
        path: "/api/windows/{name}",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, window::put),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/handlers",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, handlers::get_handlers),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/plugins",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, handlers::get_plugins),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/install",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, plugins::install),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/remove",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, plugins::remove),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/plugins/update",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, plugins::update),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/catalogs/copy",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, catalogs::copy),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/catalogs/delete",
        listeners: LOCAL,
        who: Who::Preferences,
        handler: |method| on(method, catalogs::delete),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/resolvers/{id}/scan",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, handlers::scan),
    },
    Route {
        method: MethodFilter::POST,
        path: "/api/resolvers/{id}/resolve",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, resolve::resolve),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/workspaces/recent",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, workspaces::recent),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/thumbnail",
        listeners: MEMBERS,
        who: Who::Files,
        handler: |method| on(method, thumbnails::get),
    },
    Route {
        method: MethodFilter::GET,
        path: "/api/sessions",
        listeners: MEMBERS,
        who: Who::Sessions,
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
