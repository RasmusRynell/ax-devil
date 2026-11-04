//! The route table against the real listeners: every row is called with every method as every kind of caller, and the
//! status must be the one the table promises.

use super::{Listener, ROUTES};
use crate::{
    AppState,
    access::{Policy, Principal},
    app, lan_app,
    plugins::Plugins,
};
use axum::{
    Router,
    body::Body,
    extract::ConnectInfo,
    http::{Method, Request, StatusCode},
    routing::MethodFilter,
};
use handlers::Handlers;
use sessions::Secret;
use std::{net::SocketAddr, sync::Arc};
use tower::ServiceExt;

const PASSWORD: &str = "secret";

/// A kind of caller and the listener it reaches.
struct Caller {
    name: &'static str,
    listener: Listener,
    /// What the listener makes of the caller.
    principal: Principal,
    headers: &'static [(&'static str, &'static str)],
}

/// The four kinds of caller from the access design: the person at this machine, someone with only a join link on the
/// listener for other machines, a holder of the server password, and a caller with no credential on a server.
fn callers() -> [Caller; 4] {
    [
        Caller {
            name: "local",
            listener: Listener::Local,
            principal: Principal::Local,
            headers: &[("host", "127.0.0.1")],
        },
        Caller {
            name: "link",
            listener: Listener::Lan,
            principal: Principal::None,
            headers: &[("host", "192.168.1.20:7340")],
        },
        Caller {
            name: "password",
            listener: Listener::Server,
            principal: Principal::Server,
            headers: &[("authorization", "Bearer secret")],
        },
        Caller {
            name: "nobody",
            listener: Listener::Server,
            principal: Principal::None,
            headers: &[("authorization", "Bearer guess")],
        },
    ]
}

fn router(listener: Listener) -> (Router, tempfile::TempDir, tokio::sync::watch::Sender<bool>) {
    let folder = tempfile::tempdir().unwrap();
    let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (stop, stopping) = tokio::sync::watch::channel(false);
    let policy = match listener {
        Listener::Local => Policy::Local,
        Listener::Lan => Policy::Shared { password: None },
        Listener::Server => Policy::Shared {
            password: Some(Secret::chosen(PASSWORD.into()).unwrap()),
        },
    };
    let state = AppState::new(
        Arc::new(settings),
        policy,
        videos,
        stopping,
        Plugins::fixed(Handlers::built_in()),
        &cache,
    )
    .with_network(crate::test_support::idle_network());
    let router = match listener {
        Listener::Lan => lan_app(state),
        Listener::Local | Listener::Server => app(state),
    };
    (router, folder, stop)
}

async fn status(router: &Router, method: &Method, path: &str, caller: &Caller) -> StatusCode {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json");
    for (name, value) in caller.headers {
        request = request.header(*name, *value);
    }
    let mut request = request.body(Body::from("{}")).unwrap();
    request
        .extensions_mut()
        .insert(ConnectInfo(SocketAddr::from(([10, 0, 0, 9], 4000))));
    router.clone().oneshot(request).await.unwrap().status()
}

#[tokio::test]
async fn every_route_answers_every_caller_as_the_table_says() {
    let mut paths: Vec<_> = ROUTES.iter().map(|route| route.path).collect();
    paths.dedup();
    for caller in callers() {
        let (router, _folder, _stop) = router(caller.listener);
        for path in &paths {
            for method in [Method::GET, Method::POST, Method::PUT, Method::DELETE] {
                let got = status(&router, &method, path, &caller).await;
                let at = format!("{method} {path} as {}", caller.name);
                let served: Vec<_> = ROUTES
                    .iter()
                    .filter(|route| {
                        route.path == *path && route.listeners.contains(&caller.listener)
                    })
                    .collect();
                let row = served.iter().find(|route| {
                    MethodFilter::try_from(method.clone()).ok() == Some(route.method)
                });
                if served.is_empty() {
                    // The listener has no such route; release builds answer from the assets, which take GET only.
                    let unmounted = if cfg!(feature = "embed-assets") && method != Method::GET {
                        StatusCode::METHOD_NOT_ALLOWED
                    } else {
                        StatusCode::NOT_FOUND
                    };
                    assert_eq!(got, unmounted, "{at}");
                } else if let Some(row) = row {
                    if row.who.allows(caller.principal) {
                        // Past the gate. What the handler makes of an empty request is its own business, and a path
                        // that names a thing (`{item}`) may say there is no such thing.
                        let refused = [
                            StatusCode::UNAUTHORIZED,
                            StatusCode::FORBIDDEN,
                            StatusCode::METHOD_NOT_ALLOWED,
                        ];
                        let missing = got == StatusCode::NOT_FOUND && !path.contains('{');
                        assert!(!refused.contains(&got) && !missing, "{at}: {got}");
                    } else {
                        assert_eq!(got, StatusCode::UNAUTHORIZED, "{at}");
                    }
                } else {
                    assert_eq!(got, StatusCode::METHOD_NOT_ALLOWED, "{at}");
                }
            }
        }
    }
}
