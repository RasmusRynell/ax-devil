use super::*;
use crate::inspect::FieldWire;
use scene::{
    OperationKind,
    inspection::{FieldGroup, FieldOrigin},
};
use scene_store::{Op, SortKey};
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
            basis: None,
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
            "policy": {"basis": null, "tolerance_ms": null, "sticky": {"enabled": true, "timeout_ms": 2050}}
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
                    group: FieldGroup::Debug,
                    origin: Some(FieldOrigin::Source),
                    path: vec!["tags".into(), "0".into()],
                    value: json!("x"),
                },
                FieldWire {
                    group: FieldGroup::Classification,
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
                slot: "gate.mp4".into(),
                source: None,
                policy: None,
            },
            LaneInfo {
                label: "a.txt".into(),
                slot: "a.txt".into(),
                source: Some(0),
                policy: None,
            },
        ],
        lane_defaults: LaneDefaultsWire {
            catalog: "built-in:standard".into(),
            policy: LanePolicyWire {
                basis: None,
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
        video_pts: Some(VideoPtsWire {
            time_base: [1, 90_000],
            first_pts: 7_200,
        }),
    };
    let json = serde_json::to_value(&opened).unwrap();
    assert_eq!(json["protocol"], 6);
    assert_eq!(
        json["video_pts"],
        json!({"time_base": [1, 90_000], "first_pts": 7_200})
    );
    assert_eq!(json["catalogs"][0]["id"], "built-in:standard");
    assert_eq!(
        json["lanes"],
        json!([{"label": "gate.mp4", "slot": "gate.mp4", "source": null}, {"label": "a.txt", "slot": "a.txt", "source": 0}])
    );
    assert_eq!(json["lane_defaults"]["catalog"], "built-in:standard");
    assert_eq!(
        json["lane_defaults"]["policy"]["sticky"]["timeout_ms"],
        2050
    );
    let ready = ServerMessage::LaneStatus {
        lane: 1,
        status: LaneStatusWire::Ready {
            bases: vec![BasisWire::Frame],
            default_basis: BasisWire::Frame,
            filter: Vec::new(),
            warnings: Vec::new(),
            alignment: None,
            samples: 6,
            data_revision: 1,
            mean_sample_bytes: 812,
            ingest: IngestWire::Reading,
        },
    };
    assert_eq!(
        serde_json::to_value(&ready).unwrap(),
        json!({"type": "LaneStatus", "lane": 1, "status": {
            "status": "Ready", "bases": ["Frame"], "default_basis": "Frame", "filter": [], "warnings": [], "alignment": null,
            "samples": 6,
            "data_revision": 1, "mean_sample_bytes": 812, "ingest": {"state": "Reading"}
        }})
    );
    let stopped = IngestWire::Incomplete(ErrorWire {
        code: foundation::ErrorCode::OverlayInvalid,
        params: crate::ErrorParams::default(),
        reason: Some("The converter stopped with exit code 3".into()),
        location: None,
    });
    assert_eq!(
        serde_json::to_value(&stopped).unwrap(),
        json!({"state": "Incomplete", "code": "OverlayInvalid", "params": {},
               "reason": "The converter stopped with exit code 3"})
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

#[test]
fn cached_frames_are_runs_of_first_and_last() {
    let cached = ServerMessage::Cached {
        ranges: vec![[0, 4], [9, 9]],
    };
    assert_eq!(
        serde_json::to_value(&cached).unwrap(),
        json!({"type": "Cached", "ranges": [[0, 4], [9, 9]]})
    );
}

#[test]
fn export_messages_use_the_names_the_frontend_parses() {
    let start = json!({
        "type": "Export", "job": 4, "first": 10, "last": 99, "path": "/home/a/out.mp4",
        "format": {"format": "Mp4", "quality": "balanced"},
        "lanes": [{
            "lane": 1, "policy_revision": 2, "data_revision": null,
            "catalog": {"id": "built-in:standard", "revision": 3},
            "style": {
                "label": "a.txt", "enabled_classes": [0, 2], "class_options": 3, "hidden": 1,
                "theme": null, "retained_opacity": 0.5, "filter": "person"
            }
        }]
    });
    let ClientMessage::Export {
        lanes,
        format,
        last,
        ..
    } = serde_json::from_value(start).unwrap()
    else {
        panic!("not an Export");
    };
    assert_eq!(last, 99);
    assert_eq!(
        format,
        ExportFormatWire::Mp4 {
            quality: ExportQualityWire::Balanced
        }
    );
    assert_eq!(lanes[0].style.enabled_classes, [0, 2]);
    assert_eq!(lanes[0].style.filter.as_deref(), Some("person"));
    assert_eq!(
        serde_json::to_value(ExportFormatWire::Png).unwrap(),
        json!({"format": "Png"})
    );
    let progress = ServerMessage::Export {
        job: 4,
        state: ExportStateWire::Running { done: 5, total: 90 },
    };
    assert_eq!(
        serde_json::to_value(&progress).unwrap(),
        json!({"type": "Export", "job": 4, "state": {"state": "Running", "done": 5, "total": 90}})
    );
    let failed = ServerMessage::Export {
        job: 4,
        state: ExportStateWire::Failed(ErrorWire {
            code: foundation::ErrorCode::OverlayInvalid,
            params: crate::ErrorParams::default(),
            reason: Some("disk full".into()),
            location: None,
        }),
    };
    assert_eq!(
        serde_json::to_value(&failed).unwrap()["state"]["state"],
        "Failed"
    );
}

#[test]
fn file_scope_messages_use_the_names_the_frontend_parses() {
    let query = json!({
        "type": "FileQuery", "lane": 1, "request": 7, "classes": [5],
        "query": {"groups": [[{"path": null, "op": "ge", "value": "3", "negated": true}]]},
        "sort": {"key": "first_seen", "descending": true}, "offset": 20, "limit": 100, "bins": 64
    });
    let ClientMessage::FileQuery {
        query: search,
        sort,
        ..
    } = serde_json::from_value(query).unwrap()
    else {
        panic!("a FileQuery");
    };
    assert_eq!(search.groups[0][0].op, Op::Ge);
    assert_eq!(sort.key, SortKey::FirstSeen);

    assert!(matches!(
        serde_json::from_value(json!({"type": "FileRelease", "lane": 1})).unwrap(),
        ClientMessage::FileRelease { lane: 1 }
    ));

    let refused = ServerMessage::FileScope {
        lane: 1,
        request: 7,
        unavailable: FileScopeUnavailable::Following,
    };
    assert_eq!(
        serde_json::to_value(&refused).unwrap(),
        json!({"type": "FileScope", "lane": 1, "request": 7, "unavailable": {"state": "Following"}})
    );
    let rows = ServerMessage::FileRows {
        lane: 1,
        request: 7,
        data_revision: 2,
        policy_revision: 3,
        total: 9,
        matched: 1,
        offset: 0,
        rows: vec![Row {
            id: "4".into(),
            classes: vec!["car".into()],
            events: vec![OperationKind::Merge],
            first_frame: 3,
            last_frame: 8,
            presence: vec![[3, 5], [7, 8]],
        }],
    };
    let json = serde_json::to_value(&rows).unwrap();
    assert_eq!(json["rows"][0]["events"], json!(["merge"]));
    assert_eq!(json["rows"][0]["presence"], json!([[3, 5], [7, 8]]));
}
