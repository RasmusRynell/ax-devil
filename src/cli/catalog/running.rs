//! The shell's local-port discovery, reused for the CLI's single-key Owner save.
use super::{Home, Report, manage};
use catalogs::DEFAULT_SETTING;
use serde_json::{Value, json};
use std::{path::Path, time::Duration};

/// Saves through a matching backend on the shell's PORT; only an absent or confirmed different backend permits a file write.
pub async fn use_default(catalog: &Path, home: Result<Home, String>) -> Report {
    match save(catalog, home).await {
        Ok(report) => report,
        Err(reason) => Report {
            text: format!("{reason}\n"),
            ok: false,
        },
    }
}

async fn save(catalog: &Path, home: Result<Home, String>) -> Result<Report, String> {
    let home = home?;
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()
        .map_err(|_| "PORT must be a port number".to_owned())?;
    save_at(catalog, &home, port).await
}

async fn save_at(catalog: &Path, home: &Home, port: u16) -> Result<Report, String> {
    let base = format!("http://127.0.0.1:{port}");
    let failure = || {
        format!(
            "Cannot use the backend at {base}; no settings file was written. Check PORT and the running app."
        )
    };
    let Some(mut backend) = Backend::connect(port).await? else {
        // As in the shell, this is a point-in-time port probe, not a cross-process lock.
        let mut report = manage::use_catalog(home, catalog)?;
        if report.ok {
            report
                .text
                .push_str("Saved to the settings file (no local backend running).\n");
        }
        return Ok(report);
    };
    let (status, info) = backend.request("GET", "/api/info", Value::Null).await?;
    if status != 200
        || info["app"] != "ax-devil"
        || info["api"] != api_types::api_version()
        || info["shell"] != api_types::SHELL_API
        || info["owner"] != true
    {
        return Err(format!(
            "The backend at {base} is incompatible or refuses Owner access; no settings file was written."
        ));
    }
    let unknown = || {
        "Cannot establish configuration identity for the requested file or the running backend; no settings file was written. Check the configuration path and its parent folders.".to_owned()
    };
    let requested = home.settings.identity().ok_or_else(unknown)?;
    let serving = info["configIdentity"]
        .as_str()
        .filter(|id| id.len() == 64 && id.bytes().all(|byte| byte.is_ascii_hexdigit()))
        .ok_or_else(unknown)?;
    if serving != requested {
        // Discovery sees only PORT. Only a confirmed different configuration takes the no-match file path.
        let mut report = manage::use_catalog(home, catalog)?;
        if report.ok {
            report.text.push_str(
                "Saved to the requested settings file (no matching local backend configuration).\n",
            );
        }
        return Ok(report);
    }
    let (stored, mut report) = manage::selection(home, catalog)?;
    let Some(stored) = stored else {
        return Ok(report);
    };
    let id = if stored.is_empty() {
        "built-in:standard"
    } else {
        &stored
    };
    let (status, fresh) = backend
        .request("POST", "/api/catalogs/reload", json!({"id": id}))
        .await?;
    if status != 200 {
        return Err(failure());
    }
    if id.starts_with("user:") {
        let text = fresh["document"].as_str().ok_or_else(failure)?;
        let checked = manage::check_text(text, Path::new(id))?;
        if !checked.ok {
            return Ok(checked);
        }
    }
    let revision = fresh["revision"].as_u64().ok_or_else(failure)?;
    let (status, _) = backend
        .request(
            "PATCH",
            "/api/settings",
            json!({"set": {DEFAULT_SETTING: stored}, "catalogRevision": revision}),
        )
        .await?;
    if status != 200 {
        return Err(format!(
            "The backend at {base} refused the default catalog (HTTP {status}); no settings file was written."
        ));
    }
    report.text.push_str(&format!(
        "Saved through the running local backend at {base}.\n"
    ));
    Ok(report)
}

// One pinned loopback connection for discovery and writes: a replacement process on PORT cannot receive a
// PATCH intended for the backend whose configuration was checked. No credentials, proxies or redirects.
struct Backend {
    port: u16,
    sender: hyper::client::conn::http1::SendRequest<http_body_util::Full<bytes::Bytes>>,
    connection: tokio::task::JoinHandle<Result<(), hyper::Error>>,
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.connection.abort();
    }
}

impl Backend {
    async fn connect(port: u16) -> Result<Option<Self>, String> {
        tokio::time::timeout(Duration::from_secs(5), async {
            let stream = match tokio::net::TcpStream::connect(("127.0.0.1", port)).await {
                Ok(stream) => stream,
                Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => {
                    return Ok(None);
                }
                Err(_) => return Err(unreachable(port)),
            };
            let (sender, connection) =
                hyper::client::conn::http1::handshake(hyper_util::rt::TokioIo::new(stream))
                    .await
                    .map_err(|_| unreachable(port))?;
            Ok(Some(Self {
                port,
                sender,
                connection: tokio::spawn(connection),
            }))
        })
        .await
        .map_err(|_| unreachable(port))?
    }

    async fn request(
        &mut self,
        method: &str,
        path: &str,
        body: Value,
    ) -> Result<(u16, Value), String> {
        use http_body_util::{BodyExt, Full};
        let port = self.port;
        tokio::time::timeout(Duration::from_secs(5), async {
            let payload = if body.is_null() {
                String::new()
            } else {
                body.to_string()
            };
            let sent = hyper::Request::builder()
                .method(method)
                .uri(path)
                .header("host", format!("127.0.0.1:{port}"))
                .header("content-type", "application/json")
                .body(Full::new(bytes::Bytes::from(payload)))
                .map_err(|_| unreachable(port))?;
            // Consuming a response body can wake this task before the connection dispatcher is ready again.
            self.sender.ready().await.map_err(|_| unreachable(port))?;
            let response = self
                .sender
                .send_request(sent)
                .await
                .map_err(|_| unreachable(port))?;
            let status = response.status().as_u16();
            let body = http_body_util::Limited::new(response.into_body(), 4 * 1024 * 1024)
                .collect()
                .await
                .map_err(|_| unreachable(port))?
                .to_bytes();
            let value = serde_json::from_slice(&body).map_err(|_| unreachable(port))?;
            Ok((status, value))
        })
        .await
        .map_err(|_| unreachable(port))?
    }
}

fn unreachable(port: u16) -> String {
    format!("Cannot reach the local backend on PORT={port}; no settings file was written.")
}
