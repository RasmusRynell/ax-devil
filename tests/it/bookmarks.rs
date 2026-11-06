//! Bookmarks over the Session socket: marked, edited, exported and saved with the workspace.

use crate::session_client::Socket;
use crate::workspace_session::*;
use serde_json::{Value, json};
use workspace_files::SUFFIX;

fn bookmark(seq: u64, command: Value) -> Value {
    json!({"type": "command", "seq": seq, "cmd": {"type": "content", "command": {
        "type": "bookmark", "command": command}}})
}

#[tokio::test]
async fn a_marked_frame_is_listed_edited_exported_as_csv_and_saved_with_the_workspace() {
    let folder = tempfile::tempdir().unwrap();
    let mut window = Socket::own(&backend(folder.path(), None).await, None).await;
    window.say(add_video(1)).await;
    let ready = doc(&mut window, |ws| {
        ws["items"][0]["resolution"]["status"] == "Resolved"
    })
    .await;
    let item = ready["items"][0]["id"].as_str().unwrap().to_owned();

    // A frame of the video, an entry the video lacks, and a negative time.
    let gone = format!("{item}/list/gone");
    for (seq, entry, time_us) in [
        (2, item.as_str(), 3_000_000),
        (3, gone.as_str(), 0),
        (4, &item, -1),
    ] {
        let add =
            json!({"type": "add", "item": item, "entry": entry, "frame": 75, "timeUs": time_us});
        window.say(bookmark(seq, add)).await;
    }
    let listed = doc(&mut window, |ws| {
        !ws["bookmarks"].as_array().unwrap().is_empty()
    })
    .await;
    let [group] = listed["bookmarks"].as_array().unwrap().as_slice() else {
        panic!("expected one group");
    };
    assert_eq!(group["standing"]["status"], "Open");
    let marks = group["bookmarks"].as_array().unwrap();
    assert_eq!(marks.len(), 1, "only the valid frame is marked");
    let id = marks[0]["id"].as_str().unwrap();
    assert_eq!(
        (&marks[0]["frame"], &marks[0]["time_us"]),
        (&json!(75), &json!(3_000_000))
    );

    let edit = json!({"type": "edit", "id": id, "note": "blur, \"here\"", "kind": "issue"});
    window.say(bookmark(5, edit)).await;
    doc(&mut window, |ws| {
        ws["bookmarks"][0]["bookmarks"][0]["kind"] == "issue"
    })
    .await;

    // The CSV is written to the host, the whole of it or not at all.
    let csv = folder.path().join("marks.csv");
    let export = |path: &std::path::Path| {
        workspace(
            6,
            json!({"type": "exportBookmarks", "path": path.display().to_string()}),
        )
    };
    window.say(export(&csv)).await;
    assert_eq!(ack(&mut window, 6).await, json!({"status": "ok"}));
    assert_eq!(
        std::fs::read_to_string(&csv).unwrap(),
        "target,entry,frame,time,kind,note\r\ncfr_h264_gop10_bf2.mp4,,75,3.000,Issue,\"blur, \"\"here\"\"\"\r\n"
    );
    window
        .say(export(&folder.path().join("missing").join("marks.csv")))
        .await;
    assert_eq!(ack(&mut window, 6).await["status"], "failed");

    // Only a CSV file is written, so the export cannot overwrite another kind of file.
    let other = folder.path().join("authorized_keys");
    window.say(export(&other)).await;
    let refused = ack(&mut window, 6).await;
    assert_eq!(refused["error"]["code"], "InvalidPath");
    assert!(!other.exists());

    // Saved with the workspace, the mark is still there when it opens again.
    let saved = folder.path().join(format!("gate{SUFFIX}"));
    let path = saved.display().to_string();
    window
        .say(workspace(7, json!({"type": "save", "path": path})))
        .await;
    assert_eq!(ack(&mut window, 7).await, json!({"status": "ok"}));
    window
        .say(workspace(8, json!({"type": "open", "path": path})))
        .await;
    let reopened = doc(&mut window, |ws| {
        ws["epoch"].as_u64() > ready["epoch"].as_u64()
            && ws["bookmarks"][0]["bookmarks"][0]["note"] == "blur, \"here\""
    })
    .await;
    assert_eq!(reopened["modified"], false);
}
