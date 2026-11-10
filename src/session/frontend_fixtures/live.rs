//! Scenarios of the Live view.

use super::{Build, Recording, Scene};
use serde_json::json;
use workspace::{Environment, LiveOverlay, RtspRoute, Secret};

/// The scenarios of this file.
pub(super) const SCENARIOS: &[(&str, Build)] = &[
    ("cameras", cameras),
    ("live-references", references),
    ("live-typed", typed),
    ("live-found", found),
    ("live-states", states),
];

/// The variables the references of these scenarios read; `$UNSET` is not one.
fn variables() -> Environment {
    Environment::new(|name| match name {
        "BROKER" => Some("198.51.100.2".into()),
        "CAM_HOST" => Some("10.0.0.6".into()),
        _ => None,
    })
}

/// An overlay of the MQTT broker `broker`.
fn mqtt(broker: &str) -> LiveOverlay {
    LiveOverlay::Mqtt {
        handler: "ADF_V1_FRAME".into(),
        broker_host: broker.into(),
        broker_port: 1883,
        broker_user: String::new(),
        broker_pass: Secret::new(""),
        source_key: "scene".into(),
    }
}

/// Two streams of one camera: `a` (Lobby, on a broker, open) and `b` (Door, video only). In the state `padded`, `b`
/// is as a workspace file holds it with its host padded as it was typed.
fn cameras() -> Recording {
    let build = |padded: bool| {
        let mut scene = Scene::new();
        scene.live_with("a", Some("Lobby"), "10.0.0.2", |recipe| {
            recipe.username = "root".into();
            recipe.password = Secret::new("$CAM_PASS");
            recipe.rtsp_route = RtspRoute::WebApi;
            recipe.overlay = mqtt("10.0.0.9");
        });
        if padded {
            let door = json!({
                "kind": "live", "label": "Door", "host": " 10.0.0.2 ", "protocol": "https", "head": 1,
                "rtsp_port": 554, "overlay": { "mode": "none" },
            });
            scene.loaded("b", door);
        } else {
            scene.live("b", Some("Door"), "10.0.0.2");
        }
        scene.open("a");
        scene
    };
    let mut recording = Recording::initial(&build(false));
    recording.state("padded", build(true).session());
    recording
}

/// Streams whose hosts are references: `a` and `b` of the host 10.0.0.5, head 2, on the brokers `$BROKER` and `$UNSET`,
/// and `c` of `$CAM_HOST`, as read in `environment`.
fn references_in(environment: Environment) -> Recording {
    let mut scene = Scene::new();
    for (key, broker) in [("a", "$BROKER"), ("b", "$UNSET")] {
        scene.live_with(key, None, "10.0.0.5", |recipe| {
            recipe.head = 2;
            recipe.overlay = mqtt(broker);
        });
    }
    scene.live("c", None, "$CAM_HOST");
    let mut recording = Recording::new(&scene);
    recording.environment = environment;
    recording.state("initial", scene.session());
    recording
}

/// The references as an owner's session reads them: `$BROKER` and `$CAM_HOST` have values, `$UNSET` has none.
fn references() -> Recording {
    references_in(variables())
}

/// The same streams in a session that reads no environment: every reference reads as typed.
fn typed() -> Recording {
    references_in(Environment::literal())
}

/// Streams `r` of `$CAM_HOST`, `e` of 10.0.0.5 and `p` of 10.0.0.8, which is still resolving.
fn found() -> Recording {
    let mut scene = Scene::new();
    scene.live("r", None, "$CAM_HOST");
    scene.live("e", None, "10.0.0.5");
    scene.live("p", None, "10.0.0.8");
    scene.resolving("p");
    let mut recording = Recording::new(&scene);
    recording.environment = variables();
    recording.state("initial", scene.session());
    recording
}

/// A stream `a` of 10.0.0.2, `c` (Lobby) of the same camera and `d` (Lobby) with no host, which fails; then `c` is
/// being read again, as `resolving`.
fn states() -> Recording {
    let mut scene = Scene::new();
    scene.live("a", None, "10.0.0.2");
    scene.live("c", Some("Lobby"), "10.0.0.2");
    scene.live("d", Some("Lobby"), "");
    let mut recording = Recording::initial(&scene);
    scene.resolving("c");
    recording.state("resolving", scene.session());
    recording
}
