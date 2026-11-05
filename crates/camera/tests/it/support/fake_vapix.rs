//! An in-process Axis device API on 127.0.0.1: Digest login, device info, parameters, snapshot, analytics sources.

use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use camera::{Camera, Host, Login, Scheme, Secret, Web};
use tokio::net::TcpListener;
use tokio::task::JoinHandle;

use super::digest::{self, Algorithm};
use super::fake_analytics::Analytics;

pub const USER: &str = "root";
pub const PASSWORD: &str = "pass-4711";
pub const JPEG: &[u8] = &[
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, b'J', b'F', b'I', b'F', 0xFF, 0xD9,
];
const REALM: &str = "AXIS_ACCC8E000001";
const NONCE: &str = "dcd98b7102dd2f0e8b11d0f600bfb0c093";

/// How the fake behaves.
#[derive(Clone)]
pub struct Behaviour {
    pub algorithm: Algorithm,
    /// The `Brand` it reports.
    pub brand: &'static str,
    /// Answer every request with 404, like a web server that is not a camera.
    pub not_found: bool,
    /// Whether even the unrestricted device info wants a login.
    pub login_for_info: bool,
    /// Answer every request with a redirect to this URL.
    pub redirect_to: Option<String>,
    /// Wait this long before answering.
    pub delay: Duration,
    /// Adding an analytics publisher takes this long to apply, and applies even if the client stops waiting.
    pub slow_create: Duration,
    /// Answer this many requests that carry a login with 429 before accepting any.
    pub throttle_logins: usize,
    /// Answer a snapshot with a chunked body that never ends.
    pub endless_snapshot: bool,
    /// Answer the device info with a chunked body that never ends.
    pub endless_info: bool,
}

impl Default for Behaviour {
    fn default() -> Self {
        Self {
            algorithm: Algorithm::Md5,
            brand: "AXIS",
            not_found: false,
            login_for_info: false,
            redirect_to: None,
            delay: Duration::ZERO,
            slow_create: Duration::ZERO,
            throttle_logins: 0,
            endless_snapshot: false,
            endless_info: false,
        }
    }
}

/// One request the fake saw.
#[derive(Clone, Debug)]
pub struct Seen {
    pub method: String,
    pub target: String,
    pub authorization: Option<String>,
}

struct Shared {
    behaviour: Behaviour,
    throttle_left: AtomicUsize,
    seen: Mutex<Vec<Seen>>,
    analytics: Mutex<Analytics>,
}

/// A running fake; dropping it stops it.
pub struct FakeVapix {
    pub addr: SocketAddr,
    shared: Arc<Shared>,
    task: JoinHandle<()>,
}

impl Drop for FakeVapix {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl FakeVapix {
    pub async fn start() -> Self {
        Self::with(Behaviour::default()).await
    }

    pub async fn with(behaviour: Behaviour) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("address");
        let shared = Arc::new(Shared {
            throttle_left: AtomicUsize::new(behaviour.throttle_logins),
            behaviour,
            seen: Mutex::new(Vec::new()),
            analytics: Mutex::new(Analytics::default()),
        });
        let app = Router::new().fallback(handle).with_state(shared.clone());
        let task = tokio::spawn(async move {
            axum::serve(listener, app).await.expect("serve");
        });
        Self { addr, shared, task }
    }

    /// A camera pointing at this fake, with the right login.
    pub fn camera(&self) -> Camera {
        let mut camera = self.camera_without_login();
        camera.login = Some(Login {
            user: USER.into(),
            password: Secret::new(PASSWORD),
        });
        camera
    }

    pub fn camera_without_login(&self) -> Camera {
        let mut camera = Camera::new(Host::parse("127.0.0.1").expect("host"));
        camera.web = Web {
            scheme: Scheme::Http,
            port: self.addr.port(),
        };
        camera.timeout = Duration::from_secs(5);
        camera
    }

    /// The camera's MQTT client and publishers now.
    pub fn analytics<T>(&self, with: impl FnOnce(&mut Analytics) -> T) -> T {
        with(&mut self.shared.analytics.lock().expect("lock"))
    }

    /// Every request seen so far.
    pub fn seen(&self) -> Vec<Seen> {
        self.shared.seen.lock().expect("lock").clone()
    }

    /// How many requests carried a login.
    pub fn logins_seen(&self) -> usize {
        self.seen()
            .iter()
            .filter(|seen| seen.authorization.is_some())
            .count()
    }
}

async fn handle(State(shared): State<Arc<Shared>>, request: Request) -> Response {
    let behaviour = &shared.behaviour;
    let method = request.method().as_str().to_owned();
    let target = request
        .uri()
        .path_and_query()
        .map_or_else(String::new, ToString::to_string);
    let authorization = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    shared.seen.lock().expect("lock").push(Seen {
        method: method.clone(),
        target: target.clone(),
        authorization: authorization.clone(),
    });
    tokio::time::sleep(behaviour.delay).await;
    if let Some(url) = &behaviour.redirect_to {
        return (StatusCode::FOUND, [(header::LOCATION, url.as_str())]).into_response();
    }
    if behaviour.not_found {
        return StatusCode::NOT_FOUND.into_response();
    }
    let path = request.uri().path().to_owned();
    let body = axum::body::to_bytes(request.into_body(), 1 << 16)
        .await
        .unwrap_or_default();
    let unrestricted = path == "/axis-cgi/systemready.cgi"
        || path == "/axis-cgi/basicdeviceinfo.cgi"
            && !behaviour.login_for_info
            && String::from_utf8_lossy(&body).contains("getAllUnrestrictedProperties");
    if !unrestricted && !logged_in(behaviour, authorization.as_deref(), &method, &target) {
        let challenge = format!(
            r#"Digest realm="{REALM}", qop="auth", algorithm={}, nonce="{NONCE}""#,
            behaviour.algorithm.token()
        );
        return (
            StatusCode::UNAUTHORIZED,
            [(header::WWW_AUTHENTICATE, challenge)],
        )
            .into_response();
    }
    let throttled = !unrestricted
        && shared
            .throttle_left
            .try_update(Ordering::SeqCst, Ordering::SeqCst, |left| {
                left.checked_sub(1)
            })
            .is_ok();
    if throttled {
        return StatusCode::TOO_MANY_REQUESTS.into_response();
    }
    let analytics =
        if method == "POST" && path.ends_with("/publishers") && !behaviour.slow_create.is_zero() {
            // Detached: once the camera has the request it applies it, whether or not the client waits for the answer.
            let (shared, method, path) = (Arc::clone(&shared), method.clone(), path.clone());
            tokio::spawn(async move {
                tokio::time::sleep(shared.behaviour.slow_create).await;
                let mut analytics = shared.analytics.lock().expect("lock");
                analytics.answer(&method, &path, &body)
            })
            .await
            .expect("apply")
        } else {
            let mut analytics = shared.analytics.lock().expect("lock");
            analytics.answer(&method, &path, &body)
        };
    match analytics {
        Some(json) => (
            [(header::CONTENT_TYPE, "application/json")],
            Body::from(json),
        )
            .into_response(),
        None => answer(behaviour, &path, unrestricted),
    }
}

fn logged_in(
    behaviour: &Behaviour,
    authorization: Option<&str>,
    method: &str,
    target: &str,
) -> bool {
    authorization.is_some_and(|header| {
        digest::verify(
            header,
            behaviour.algorithm,
            (REALM, NONCE),
            (USER, PASSWORD),
            (method, target),
        )
    })
}

fn answer(behaviour: &Behaviour, path: &str, unrestricted: bool) -> Response {
    let json = |text: String| {
        (
            [(header::CONTENT_TYPE, "application/json")],
            Body::from(text),
        )
            .into_response()
    };
    match path {
        "/axis-cgi/basicdeviceinfo.cgi" if behaviour.endless_info => {
            let chunks = futures_util::stream::repeat_with(|| {
                Ok::<_, std::convert::Infallible>(bytes::Bytes::from(vec![b' '; 64 << 10]))
            });
            ([(header::CONTENT_TYPE, "application/json")], Body::from_stream(chunks)).into_response()
        }
        "/axis-cgi/basicdeviceinfo.cgi" => {
            // Only a signed-in user is told the hardware.
            let hardware = if unrestricted {
                ""
            } else {
                r#","Architecture":"aarch64","Soc":"Ambarella CV25""#
            };
            json(format!(
                r#"{{"apiVersion":"1.3","data":{{"propertyList":{{"Brand":"{}","ProdShortName":"AXIS Q1656","SerialNumber":"ACCC8E000001","Version":"11.11.1","ProdType":"Box Camera"{hardware}}}}}}}"#,
                behaviour.brand
            ))
        }
        "/axis-cgi/systemready.cgi" => {
            json(r#"{"apiVersion":"1.0","data":{"systemready":"yes","uptime":"93784"}}"#.into())
        }
        "/axis-cgi/param.cgi" => "root.Properties.Image.Resolution=1920x1080,1280x720\r\n\
             root.Properties.Image.I0.H264.Resolution=1920x1080,1280x720,640x360\r\n\
             root.Properties.Image.I1.H264.Resolution=800x600\r\n"
            .into_response(),
        "/axis-cgi/jpg/image.cgi" if behaviour.endless_snapshot => {
            let chunks = futures_util::stream::repeat_with(|| {
                Ok::<_, std::convert::Infallible>(bytes::Bytes::from(vec![0xFF; 64 << 10]))
            });
            ([(header::CONTENT_TYPE, "image/jpeg")], Body::from_stream(chunks)).into_response()
        }
        "/axis-cgi/jpg/image.cgi" => ([(header::CONTENT_TYPE, "image/jpeg")], JPEG.to_vec()).into_response(),
        "/config/rest/analytics-mqtt/v1beta/data_sources" => json(
            r#"{"status":"success","data":[{"key":"com.axis.analytics_scene_description.v0.beta#1"},{"key":"com.axis.consolidated_track.v1.beta#1"}]}"#.into(),
        ),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}
