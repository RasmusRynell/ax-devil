//! A plugin task run over the lanes of an entry, and a bounded read of one lane, over the real routes.

use crate::common::{Backend, fixture};
use crate::plugins::{install, plugin, python_available, started};
use ax_devil::plugins::Plugins;
use handlers::Handlers;

/// The body of what the local backend answers a request with `body`, read to the end of the connection.
async fn request(backend: &Backend, method: &str, path: &str, body: &str) -> String {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut socket = tokio::net::TcpStream::connect(backend.address)
        .await
        .unwrap();
    // HTTP/1.0, so the body is not chunked.
    let request = format!(
        "{method} {path} HTTP/1.0\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        backend.address,
        body.len()
    );
    socket.write_all(request.as_bytes()).await.unwrap();
    let mut response = String::new();
    socket.read_to_string(&mut response).await.unwrap();
    assert!(response.starts_with("HTTP/1.0 200"), "{response}");
    response.split_once("\r\n\r\n").unwrap().1.to_owned()
}

fn json_lines(text: &str) -> Vec<serde_json::Value> {
    text.lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[tokio::test]
async fn a_task_streams_its_progress_and_its_error_and_a_lane_is_read_in_part() {
    if !python_available() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    install(home.path(), "python-tracks");
    let registry = install(home.path(), "derived-demo");
    let backend = started(&Plugins::new(Ok(registry), Handlers::built_in())).await;
    let added = backend
        .add_with_overlays(
            &fixture("cfr_h264_gop10_bf2.mp4"),
            &[plugin("python-tracks/sample.tracks.csv")],
        )
        .await;
    let entry = &added.content.entries()[0];
    let (session, item, slot) = (
        added.owner.session().to_string(),
        added.content.id().as_str(),
        &entry.overlays[0].slot,
    );

    let run = |out: &str| {
        serde_json::json!({
            "session": session, "item": item, "entry": entry.id.as_str(),
            "lanes": { "lane": slot }, "video": false, "options": { "out": out },
        })
        .to_string()
    };
    let path = "/api/tasks/derived-count/run";
    let lines = json_lines(&request(&backend, "POST", path, &run("/tmp/out")).await);
    assert_eq!(lines.len(), 3, "{lines:?}");
    assert_eq!(lines[0]["progress"]["total"], 4);
    assert_eq!(lines[1]["progress"]["text"], "demo|/tmp/out");
    assert_eq!(lines[2], serde_json::json!({ "done": {} }));

    let lines = json_lines(&request(&backend, "POST", path, &run("/tmp/fail")).await);
    assert_eq!(lines.len(), 2, "{lines:?}");
    assert_eq!(lines[0]["progress"]["text"], "Counting");
    assert!(lines[1]["error"].is_object(), "{lines:?}");

    let scenes = format!(
        "/api/lanes/scenes?session={session}&item={item}&entry={}&slot={slot}&from=1&count=2",
        entry.id.as_str()
    );
    let part = json_lines(&request(&backend, "GET", &scenes, "").await);
    let kinds: Vec<_> = part
        .iter()
        .map(|line| line.as_object().unwrap().keys().next().unwrap().as_str())
        .collect();
    assert_eq!(kinds, ["lane", "sample", "sample"]);
}
