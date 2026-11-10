//! Scenarios that need no items.

use super::{Build, Recording, Scene, content};
use serde_json::json;

/// The scenarios of this file.
pub(super) const SCENARIOS: &[(&str, Build)] = &[("empty", empty)];

/// A fresh session; then the same workspace restored from a recovery copy, where Keep leads to `kept` and Discard to
/// `discarded`, which are the Docs the backend sends after each. From `initial`, a rename of the workspace to
/// `Review 0` and then `Review 29`.
fn empty() -> Recording {
    let mut scene = Scene::new();
    let mut recording = Recording::initial(&scene);
    let rename = |name: &str| content("renameWorkspace", json!({ "name": name, "epoch": 0 }));
    let names = [
        (rename("Review 0"), "review-0"),
        (rename("Review 29"), "review-29"),
        (rename("Review 0"), "review-0"),
    ];
    recording.chain("initial", scene.session(), names);
    scene.restore();
    recording.state("recovered", scene.session());
    for (kind, to) in [("keep", "kept"), ("discard", "discarded")] {
        let command = json!({ "type": "workspace", "command": { "type": kind } });
        recording.step("recovered", &mut scene.session().clone(), command, to);
    }
    recording
}
