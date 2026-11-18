//! Source discovery and validation before MQTT configuration changes.

use super::*;

#[tokio::test]
async fn a_data_source_the_camera_does_not_offer_is_refused_before_anything_changes() {
    let mixed: Vec<String> = (0..9)
        .flat_map(|n| [String::new(), format!("scene{n}#1")])
        .chain([" ".into(), "scene0#1".into()])
        .collect();
    for (sources, expected, remaining) in [
        (vec![String::new()], vec![], 0),
        (mixed, (0..8).map(|n| format!("scene{n}#1")).collect(), 3),
        (
            vec![" ".into(), "same#1".into(), "same#1".into()],
            vec![" ".into(), "same#1".into(), "same#1".into()],
            0,
        ),
    ] {
        let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
        vapix.analytics(|a| {
            a.sources = sources
                .iter()
                .map(|key| serde_json::json!({"key": key}))
                .collect()
        });
        let before = camera_state(&vapix);
        let mut messages = open_mqtt(
            &vapix.camera(),
            &broker_for(&broker, "unused"),
            SourceKey("com.axis.nothing\n2026 ERROR forged#9".into()),
            FAST,
        );
        let Status::Failed(error) =
            status_where(&mut messages, |s| matches!(s, Status::Failed(_))).await
        else {
            unreachable!()
        };
        assert_eq!(error.kind, ErrorKind::SourceUnavailable);
        // The key is the app's, but whatever reaches an error is printed on a line of its own.
        assert!(error.to_string().contains("nothing\\n2026"), "{error}");
        assert!(!error.to_string().contains('\n'), "{error}");
        assert_eq!(camera_state(&vapix), before);
        assert_eq!(broker.connects(), 0);
        assert_eq!(error.source_alternatives, Some((expected, remaining)));
        messages.close().await;
    }
}
