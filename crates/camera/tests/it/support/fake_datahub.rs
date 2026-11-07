//! An in-process DataHub on 127.0.0.1: the session token behind a Digest login (Basic also offered when asked),
//! and the data-hub JSON-RPC over a WebSocket.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, Request, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use camera::{Camera, Host, Login, Scheme, Secret, Web};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use tokio::task::JoinHandle;

use super::digest::{self, Algorithm};
use super::fake_vapix::{PASSWORD, USER};
use super::tls_server;

const REALM: &str = "AXIS_DATAHUB";
const NONCE: &str = "dcd98b7102dd2f0e8b11d0f600bfb0c093";
const TOKEN: &str = "session-token-4711";
/// The `Authorization` header Basic sends for `USER` and `PASSWORD`.
const BASIC_LOGIN: &str = "Basic cm9vdDpwYXNzLTQ3MTE=";

/// Where a request is answered with a redirect.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Redirect {
    Token,
    Upgrade,
}

/// What a connection does once it has confirmed the subscription and sent one sample.
#[derive(Clone, Copy)]
pub enum After {
    /// Nothing more: a quiet topic.
    Idle,
    /// Closes the WebSocket.
    Close,
    /// Sends text that is not JSON instead of the sample.
    Garbage,
    /// Sends one text frame of 5 MiB instead of the sample.
    Huge,
}

#[derive(Clone)]
pub struct Behaviour {
    pub redirect: Option<Redirect>,
    /// Also offer Basic in the 401 (first, so a client that takes the first scheme would be caught).
    pub offer_basic: bool,
    /// Offer and accept Basic only, no Digest.
    pub basic_only: bool,
    /// What the first connection does after the subscription, and what every later one does.
    pub first: After,
    pub later: After,
    /// Samples sent before the subscription is confirmed.
    pub early_samples: usize,
}

impl Default for Behaviour {
    fn default() -> Self {
        Self {
            redirect: None,
            offer_basic: false,
            basic_only: false,
            first: After::Idle,
            later: After::Idle,
            early_samples: 0,
        }
    }
}

/// What the fake has seen.
#[derive(Default)]
pub struct Seen {
    /// Every `Authorization` header sent to the token endpoint.
    pub authorizations: Vec<String>,
    pub token_requests: usize,
    pub upgrades: usize,
    /// The `params` of every subscribe request.
    pub subscriptions: Vec<Value>,
}

struct Shared {
    behaviour: Behaviour,
    seen: Mutex<Seen>,
}

/// A running fake; dropping it stops it.
pub struct FakeDataHub {
    pub addr: SocketAddr,
    shared: Arc<Shared>,
    tls: bool,
    task: JoinHandle<()>,
}

impl Drop for FakeDataHub {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl FakeDataHub {
    pub async fn start() -> Self {
        Self::with(Behaviour::default()).await
    }

    /// The same camera on HTTPS with a self-signed certificate.
    pub async fn start_tls() -> Self {
        Self::serve(Behaviour::default(), true).await
    }

    pub async fn with(behaviour: Behaviour) -> Self {
        Self::serve(behaviour, false).await
    }

    async fn serve(behaviour: Behaviour, tls: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let shared = Arc::new(Shared {
            behaviour,
            seen: Mutex::new(Seen::default()),
        });
        let app = Router::new()
            .route("/axis-cgi/wssession.cgi", axum::routing::get(token))
            .route("/vapix/ws/v1", axum::routing::get(upgrade))
            .with_state(shared.clone());
        let task = tokio::spawn(async move {
            if tls {
                let listener = tls_server::TlsListener {
                    tcp: listener,
                    acceptor: tls_server::acceptor(),
                };
                axum::serve(listener, app).await.expect("serve");
            } else {
                axum::serve(listener, app).await.expect("serve");
            }
        });
        Self {
            addr,
            shared,
            tls,
            task,
        }
    }

    /// A camera pointing at this fake, with the right login.
    pub fn camera(&self) -> Camera {
        let mut camera = Camera::new(Host::parse("127.0.0.1").expect("host"));
        camera.web = Web {
            scheme: if self.tls {
                Scheme::Https
            } else {
                Scheme::Http
            },
            port: self.addr.port(),
        };
        camera.login = Some(Login {
            user: USER.into(),
            password: Secret::new(PASSWORD),
        });
        camera
    }

    pub fn seen<T>(&self, with: impl FnOnce(&Seen) -> T) -> T {
        with(&self.shared.seen.lock().expect("lock"))
    }
}

fn redirect() -> Response {
    (
        StatusCode::FOUND,
        [(header::LOCATION, "http://192.0.2.1/elsewhere")],
    )
        .into_response()
}

async fn token(State(shared): State<Arc<Shared>>, request: Request) -> Response {
    let authorization = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let target = request.uri().path().to_owned();
    {
        let mut seen = shared.seen.lock().expect("lock");
        seen.token_requests += 1;
        seen.authorizations.extend(authorization.clone());
    }
    if shared.behaviour.redirect == Some(Redirect::Token) {
        return redirect();
    }
    let allowed = authorization.as_deref().is_some_and(|header| {
        if shared.behaviour.basic_only {
            return header == BASIC_LOGIN;
        }
        digest::verify(
            header,
            Algorithm::Md5,
            (REALM, NONCE),
            (USER, PASSWORD),
            ("GET", &target),
        )
    });
    if allowed {
        return TOKEN.into_response();
    }
    let mut headers = HeaderMap::new();
    if shared.behaviour.offer_basic || shared.behaviour.basic_only {
        headers.append(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_static(r#"Basic realm="datahub""#),
        );
    }
    if !shared.behaviour.basic_only {
        let digest =
            format!(r#"Digest realm="{REALM}", qop="auth", algorithm=MD5, nonce="{NONCE}""#);
        headers.append(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_str(&digest).expect("header"),
        );
    }
    (StatusCode::UNAUTHORIZED, headers).into_response()
}

async fn upgrade(
    State(shared): State<Arc<Shared>>,
    Query(query): Query<std::collections::HashMap<String, String>>,
    socket: WebSocketUpgrade,
) -> Response {
    let index = {
        let mut seen = shared.seen.lock().expect("lock");
        seen.upgrades += 1;
        seen.upgrades - 1
    };
    if shared.behaviour.redirect == Some(Redirect::Upgrade) {
        return redirect();
    }
    let known = query.get("wssession").map(String::as_str) == Some(TOKEN)
        && query.get("apis").map(String::as_str) == Some("data-hub");
    if !known {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let after = if index == 0 {
        shared.behaviour.first
    } else {
        shared.behaviour.later
    };
    socket.on_upgrade(move |socket| conversation(socket, shared, after, index))
}

async fn conversation(mut socket: WebSocket, shared: Arc<Shared>, after: After, index: usize) {
    while let Some(Ok(Message::Text(text))) = socket.recv().await {
        let request: Value = serde_json::from_str(text.as_str()).expect("a JSON request");
        let reply = |result: Value| json!({ "jsonrpc": "2.0", "id": request["id"], "method": request["method"], "result": result });
        match request["method"].as_str() {
            Some("data-hub_v1:listTopics") => {
                let topics = json!({ "topics": [{ "name": "com.axis.scene.frame.v1" }, { "name": "other" }] });
                send(&mut socket, &reply(topics)).await;
            }
            Some("data-hub_v1:subscribe") => {
                let params = request["params"].clone();
                shared
                    .seen
                    .lock()
                    .expect("lock")
                    .subscriptions
                    .push(params.clone());
                let filter = &params["filters"][0];
                let sample_with = |data: Value| {
                    json!({ "jsonrpc": "2.0", "method": "data-hub_v1:topicSample", "params": {
                        "topic": filter["topics"][0],
                        "instance": { "channel_id": filter["instanceFilter"][0]["channel_id"] },
                        "data": data,
                        "timestamp": "2026-01-01T00:00:00Z",
                        "isHistorical": false,
                    }})
                };
                for n in 0..shared.behaviour.early_samples {
                    send(&mut socket, &sample_with(json!({ "early": n }))).await;
                }
                send(&mut socket, &reply(json!({ "subscriptionId": "sub-1" }))).await;
                let sample = sample_with(json!({ "connection": index }));
                match after {
                    After::Garbage => {
                        socket.send(Message::Text("not json".into())).await.ok();
                    }
                    After::Huge => {
                        let big = "x".repeat(5 << 20);
                        socket.send(Message::Text(big.into())).await.ok();
                    }
                    After::Idle | After::Close => send(&mut socket, &sample).await,
                }
                if matches!(after, After::Close) {
                    socket.send(Message::Close(None)).await.ok();
                    return;
                }
            }
            _ => {}
        }
    }
}

async fn send(socket: &mut WebSocket, value: &Value) {
    socket
        .send(Message::Text(value.to_string().into()))
        .await
        .ok();
}
