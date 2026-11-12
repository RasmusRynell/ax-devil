//! A resolved off-page pin stays copyable while an interrupted scan starts over.
use super::*;
use api_types::inspect::InspectionPin;

#[tokio::test]
async fn restarting_a_partial_pin_scan_preserves_resolved_copy_addresses() {
    let (_folder, viewer, revision) = pin_viewer().await;
    let mut inspections = Inspections::default();
    inspections.ask(&viewer, 0, 0, revision, 10, "cvat_track_7".into());
    let InspectionWire::Found {
        continuation: Some(cursor),
        fields,
        ..
    } = verdict(answer(&mut inspections).await.0)
    else {
        panic!("first page");
    };
    let visible = fields
        .iter()
        .find_map(|field| field.cut)
        .expect("a visible value to copy")[0] as u32;
    assert!(
        fields
            .iter()
            .all(|field| field.path.last().map(String::as_str) != Some("field05000")),
        "the resolved pin is off-page"
    );
    let wanted: Vec<_> = ["field05000", "field11000"]
        .map(|name| InspectionPin {
            group: FieldGroup::Debug,
            identity: None,
            path: ["cvat", "shape", "extra", name].map(String::from).into(),
        })
        .into();
    let read = InspectionRead {
        pins: Some(wanted.clone()),
        request: 1,
        token: cursor.token,
        field: None,
        raw: None,
        path: false,
        offset: 0,
    };
    let mut offset = 0;
    let (ordinal, scan_next) = loop {
        inspections.continue_target(
            InspectionRead {
                offset,
                ..read.clone()
            },
            viewer.inspect_target(0, revision, 10),
        );
        let ServerMessage::InspectContinued {
            result: InspectionReadResult::Pins { fields, next },
            ..
        } = answer(&mut inspections).await.0
        else {
            panic!("pin scan");
        };
        if let Some((index, field)) = fields.first() {
            assert_eq!(
                *index, 0,
                "first pin resolved while the later one is pending"
            );
            assert_eq!(fields.len(), 1);
            break (
                field.cut.expect("whole-value copy address")[0] as u32,
                next.expect("later pin still pending"),
            );
        }
        offset = next.expect("first pin exists");
    };
    let copy = InspectionRead {
        pins: None,
        field: Some(ordinal),
        offset: 0,
        ..read.clone()
    };
    // The nonempty reply above was not accepted by the client. A visible-value copy supersedes it.
    inspections.continue_target(
        InspectionRead {
            field: Some(visible),
            ..copy.clone()
        },
        viewer.inspect_target(0, revision, 10),
    );
    assert_chunk(answer(&mut inspections).await.0);
    inspections.continue_target(read.clone(), viewer.inspect_target(0, revision, 10));
    let ServerMessage::InspectContinued {
        result: InspectionReadResult::Pins { fields, next },
        ..
    } = answer(&mut inspections).await.0
    else {
        panic!("restarted scan");
    };
    assert_eq!(
        fields.len(),
        1,
        "restart replays the discarded nonempty reply before scanning reaches its field"
    );
    assert_eq!(fields[0].0, 0);
    assert_eq!(fields[0].1.cut, Some([u64::from(ordinal), 128 * 1024]));
    assert_eq!(fields[0].1.value, Leaf::Text("x".repeat(16 * 1024)));
    assert!(next.is_some_and(|next| next < scan_next));
    inspections.continue_target(copy.clone(), viewer.inspect_target(0, revision, 10));
    assert_chunk(answer(&mut inspections).await.0);
    let mut offset = next.unwrap();
    loop {
        inspections.continue_target(
            InspectionRead {
                offset,
                ..read.clone()
            },
            viewer.inspect_target(0, revision, 10),
        );
        let ServerMessage::InspectContinued {
            result: InspectionReadResult::Pins { fields, next },
            ..
        } = answer(&mut inspections).await.0
        else {
            panic!("remaining pin scan");
        };
        assert!(
            fields.iter().all(|(index, _)| *index == 1),
            "only unresolved pins are scanned again"
        );
        if let Some(next) = next {
            offset = next;
        } else {
            assert_eq!(fields.len(), 1, "the later pin resolves");
            assert_eq!(fields[0].1.value, Leaf::Text("11000".into()));
            break;
        }
    }
    inspections.continue_target(
        InspectionRead {
            pins: Some(vec![wanted[1].clone()]),
            ..read
        },
        viewer.inspect_target(0, revision, 10),
    );
    let _ = answer(&mut inspections).await;
    inspections.continue_target(copy, viewer.inspect_target(0, revision, 10));
    assert!(
        matches!(
            answer(&mut inspections).await.0,
            ServerMessage::InspectContinued {
                result: InspectionReadResult::Refused { .. },
                ..
            }
        ),
        "a changed pin set drops the old copy address"
    );
}

fn assert_chunk(message: ServerMessage) {
    let ServerMessage::InspectContinued {
        result: InspectionReadResult::Chunk { text, total, .. },
        ..
    } = message
    else {
        panic!("resolved pin remains copyable during restart: {message:?}");
    };
    assert_eq!(total, 128 * 1024);
    assert_eq!(text, "x".repeat(65_536));
}

#[tokio::test]
async fn re_pinning_the_only_pin_replays_its_value_on_the_same_paused_sample() {
    let (_folder, viewer, revision) = pin_viewer().await;
    let mut inspections = Inspections::default();
    inspections.ask(&viewer, 0, 0, revision, 10, "cvat_track_7".into());
    let InspectionWire::Found {
        continuation: Some(cursor),
        ..
    } = verdict(answer(&mut inspections).await.0)
    else {
        panic!("capability");
    };
    let read = InspectionRead {
        pins: Some(vec![InspectionPin {
            group: FieldGroup::Debug,
            identity: None,
            path: ["cvat", "shape", "extra", "field05000"]
                .map(String::from)
                .into(),
        }]),
        request: 1,
        token: cursor.token,
        field: None,
        raw: None,
        path: false,
        offset: 0,
    };
    let mut offset = 0;
    let resolved = loop {
        inspections.continue_target(
            InspectionRead {
                offset,
                ..read.clone()
            },
            viewer.inspect_target(0, revision, 10),
        );
        let ServerMessage::InspectContinued {
            result: InspectionReadResult::Pins { mut fields, next },
            ..
        } = answer(&mut inspections).await.0
        else {
            panic!("pin scan");
        };
        if next.is_none() {
            assert_eq!(fields.len(), 1);
            break fields.pop().unwrap();
        }
        offset = next.unwrap();
    };
    // Unpinning the last pin sends nothing. Re-pinning requests the same set at zero on the same capability.
    inspections.continue_target(read.clone(), viewer.inspect_target(0, revision, 10));
    let ServerMessage::InspectContinued {
        result: InspectionReadResult::Pins { fields, next },
        ..
    } = answer(&mut inspections).await.0
    else {
        panic!("re-pinned scan");
    };
    assert_eq!(
        fields.as_slice(),
        std::slice::from_ref(&resolved),
        "an existing re-pinned field must not become Not present"
    );
    assert_eq!(
        next, None,
        "a fully replayed set settles without another request"
    );
    inspections.continue_target(
        InspectionRead {
            pins: None,
            field: Some(resolved.1.cut.unwrap()[0] as u32),
            ..read
        },
        viewer.inspect_target(0, revision, 10),
    );
    assert_chunk(answer(&mut inspections).await.0);
}

async fn pin_viewer() -> (tempfile::TempDir, ViewerOverlays, u32) {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("pins.xml");
    let extra = (0..12000)
        .map(|i| {
            let value = if i == 0 || i == 5000 {
                "x".repeat(128 * 1024)
            } else {
                i.to_string()
            };
            format!("<field{i:05}>{value}</field{i:05}>")
        })
        .collect::<String>();
    let boxes = (0..90).map(|frame| format!("<box frame=\"{frame}\" outside=\"0\" xtl=\"10\" ytl=\"10\" xbr=\"30\" ybr=\"30\">{}</box>", if frame == 10 { format!("<extra>{extra}</extra>") } else { String::new() })).collect::<String>();
    std::fs::write(&path, format!("<annotations><meta><task><size>90</size><mode>interpolation</mode><original_size><width>64</width><height>48</height></original_size></task></meta><track id=\"7\" label=\"Car\">{boxes}</track></annotations>")).unwrap();
    let (mut viewer, revision) = open_overlay(OverlayRef {
        label: "Pins".into(),
        slot: "Pins".into(),
        path,
        handler: "CVAT".into(),
        options: handler_api::Settings::new(),
        metadata: Default::default(),
    })
    .await;
    hold(&mut viewer, 10).await;
    (folder, viewer, revision)
}
