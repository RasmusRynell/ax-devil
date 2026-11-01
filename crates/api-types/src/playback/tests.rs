use super::*;
use crate::inspect::{FieldGroupWire, FieldOriginWire, FieldWire};
use serde_json::json;

#[test]
fn messages_use_the_names_the_frontend_parses() {
    let open = ClientMessage::Open {
        ticket: "t1".into(),
        start_frame: None,
        data_bytes: 5_000_000_000,
    };
    assert_eq!(
        serde_json::to_value(&open).unwrap(),
        json!({"type": "Open", "ticket": "t1", "data_bytes": 5_000_000_000_u64})
    );
    let want = json!({
        "type": "Want", "playhead": 7, "ahead": 20, "behind": 5, "direction": "backward",
        "data_ahead": 30, "data_behind": 10
    });
    assert!(matches!(
        serde_json::from_value(want).unwrap(),
        ClientMessage::Want(Want {
            playhead: 7,
            direction: Direction::Backward,
            data_ahead: 30,
            data_behind: 10,
            ..
        })
    ));
    let policy = ClientMessage::LanePolicy {
        lane: 1,
        revision: 2,
        policy: LanePolicyWire {
            tolerance_ms: None,
            sticky: StickyWire {
                enabled: true,
                timeout_ms: Some(2050),
            },
        },
        subscribed: true,
    };
    assert_eq!(
        serde_json::to_value(&policy).unwrap(),
        json!({
            "type": "LanePolicy", "lane": 1, "revision": 2, "subscribed": true,
            "policy": {"tolerance_ms": null, "sticky": {"enabled": true, "timeout_ms": 2050}}
        })
    );
    let catalog = json!({"type": "Catalog", "id": "built-in:standard"});
    assert_eq!(
        serde_json::from_value::<ClientMessage>(catalog.clone()).unwrap(),
        ClientMessage::Catalog {
            id: "built-in:standard".into()
        }
    );
    assert_eq!(
        serde_json::to_value(ServerMessage::DataWindow { first: 3, last: 9 }).unwrap(),
        json!({"type": "DataWindow", "first": 3, "last": 9})
    );
    assert_eq!(
        serde_json::to_value(ServerMessage::Evict {
            source: 1,
            data_revision: 2,
            samples: vec![4, 7]
        })
        .unwrap(),
        json!({"type": "Evict", "source": 1, "data_revision": 2, "samples": [4, 7]})
    );
    let reply = ServerMessage::Catalog {
        id: "built-in:standard".into(),
        revision: 1,
        document: None,
        problem: Some("too large".into()),
    };
    assert_eq!(
        serde_json::to_value(&reply).unwrap(),
        json!({
            "type": "Catalog", "id": "built-in:standard", "revision": 1, "document": null, "problem": "too large"
        })
    );
}

#[test]
fn inspection_and_catalog_listing_use_the_names_the_frontend_parses() {
    let inspect =
        json!({"type": "Inspect", "source": 1, "data_revision": 2, "sample": 3, "entity": "7"});
    assert_eq!(
        serde_json::from_value::<ClientMessage>(inspect).unwrap(),
        ClientMessage::Inspect {
            source: 1,
            data_revision: 2,
            sample: 3,
            entity: "7".into()
        }
    );
    let found = ServerMessage::Inspected {
        source: 1,
        data_revision: 2,
        sample: 3,
        entity: "7".into(),
        inspection: InspectionWire::Found {
            fields: vec![
                FieldWire {
                    group: FieldGroupWire::Debug,
                    origin: Some(FieldOriginWire::Source),
                    path: vec!["tags".into(), "0".into()],
                    value: json!("x"),
                },
                FieldWire {
                    group: FieldGroupWire::Classification,
                    origin: None,
                    path: vec!["class".into()],
                    value: json!(null),
                },
            ],
            truncated: false,
        },
    };
    assert_eq!(
        serde_json::to_value(&found).unwrap(),
        json!({
            "type": "Inspected", "source": 1, "data_revision": 2, "sample": 3, "entity": "7",
            "inspection": {"state": "Found", "truncated": false, "fields": [
                {"group": "debug", "origin": "source", "path": ["tags", "0"], "value": "x"},
                {"group": "classification", "path": ["class"], "value": null}
            ]}
        })
    );
    for (inspection, state) in [
        (InspectionWire::Absent, "Absent"),
        (InspectionWire::Unavailable, "Unavailable"),
    ] {
        assert_eq!(
            serde_json::to_value(inspection).unwrap(),
            json!({ "state": state })
        );
    }
    let listing = ServerMessage::Catalogs {
        catalogs: vec![CatalogInfoWire {
            id: "user:mine.json".into(),
            name: "Mine".into(),
            description: String::new(),
        }],
    };
    assert_eq!(
        serde_json::to_value(&listing).unwrap(),
        json!({"type": "Catalogs", "catalogs": [{"id": "user:mine.json", "name": "Mine", "description": ""}]})
    );
}

#[test]
fn opened_and_lane_status_carry_the_source_and_data_revision() {
    let opened = ServerMessage::Opened {
        protocol: PLAYBACK_PROTOCOL,
        frame_count: 90,
        duration_us: 3_000_000,
        frame_bytes: 4608,
        width: 64,
        height: 48,
        warnings: Vec::new(),
        lanes: vec![
            LaneInfo {
                label: "gate.mp4".into(),
                source: None,
            },
            LaneInfo {
                label: "a.txt".into(),
                source: Some(0),
            },
        ],
        lane_defaults: LaneDefaultsWire {
            catalog: "built-in:standard".into(),
            policy: LanePolicyWire {
                tolerance_ms: Some(50),
                sticky: StickyWire {
                    enabled: false,
                    timeout_ms: Some(2050),
                },
            },
        },
        catalogs: vec![CatalogInfoWire {
            id: "built-in:standard".into(),
            name: "Standard".into(),
            description: String::new(),
        }],
    };
    let json = serde_json::to_value(&opened).unwrap();
    assert_eq!(json["protocol"], 5);
    assert_eq!(json["catalogs"][0]["id"], "built-in:standard");
    assert_eq!(
        json["lanes"],
        json!([{"label": "gate.mp4", "source": null}, {"label": "a.txt", "source": 0}])
    );
    assert_eq!(json["lane_defaults"]["catalog"], "built-in:standard");
    assert_eq!(
        json["lane_defaults"]["policy"]["sticky"]["timeout_ms"],
        2050
    );
    let ready = ServerMessage::LaneStatus {
        lane: 1,
        status: LaneStatusWire::Ready {
            clock: ClockWire::Frame,
            filter: Vec::new(),
            warnings: Vec::new(),
            samples: 6,
            data_revision: 1,
            mean_sample_bytes: 812,
        },
    };
    assert_eq!(
        serde_json::to_value(&ready).unwrap(),
        json!({"type": "LaneStatus", "lane": 1, "status": {
            "status": "Ready", "clock": "frame", "filter": [], "warnings": [], "samples": 6,
            "data_revision": 1, "mean_sample_bytes": 812
        }})
    );
    let failed = ServerMessage::LaneStatus {
        lane: 1,
        status: LaneStatusWire::Failed(ErrorWire {
            code: foundation::ErrorCode::OverlayInvalid,
            params: crate::ErrorParams::default(),
            reason: None,
            location: None,
        }),
    };
    assert_eq!(
        serde_json::to_value(&failed).unwrap(),
        json!({"type": "LaneStatus", "lane": 1, "status": {"status": "Failed", "code": "OverlayInvalid", "params": {}}})
    );
}
