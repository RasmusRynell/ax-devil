//! DataHub against an in-process camera: login and redirect rules, the exact subscription, terminal failures and
//! reconnects.

use crate::support;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use camera::{
    Camera, Device, ErrorKind, Feed, LiveMessages, Message, Secret, Status, open_datahub,
};
use support::fake_datahub::{After, Behaviour, FakeDataHub, Redirect};
use tokio::time::timeout;

const TOPIC: &str = "com.axis.scene.frame.v1";
const WAIT: Duration = Duration::from_secs(10);

async fn status_where(live: &mut LiveMessages, matches: impl Fn(&Status) -> bool) -> Status {
    timeout(WAIT, live.status.wait_for(|status| matches(status)))
        .await
        .expect("the status in time")
        .expect("status channel")
        .clone()
}

async fn next_item(live: &mut LiveMessages) -> Message {
    match timeout(WAIT, live.messages.recv()).await {
        Ok(Some(Feed::Item(message))) => message,
        other => panic!("expected a sample, got {other:?}"),
    }
}

fn wrong_password(mut camera: Camera) -> Camera {
    if let Some(login) = &mut camera.login {
        login.password = Secret::new("not-the-password");
    }
    camera
}

#[tokio::test]
async fn exactly_the_chosen_topic_and_channel_is_subscribed_and_a_quiet_topic_is_not_an_error() {
    let fake = FakeDataHub::start().await;
    let mut live = open_datahub(&fake.camera(), "chosen", 7);
    assert_eq!(
        status_where(&mut live, |s| matches!(s, Status::Live { .. })).await,
        Status::Live { epoch: 0 }
    );
    let sample = next_item(&mut live).await;
    assert_eq!(sample.topic, "chosen");
    assert_eq!(sample.epoch, 0);
    let stamp = sample.stamp.expect("a DataHub sample carries its stamp");
    assert_eq!(
        (stamp.channel, stamp.timestamp.as_str(), stamp.historical),
        (7, "2026-01-01T00:00:00Z", false)
    );
    assert_eq!(sample.payload.as_ref(), br#"{"connection":0}"#);

    // The topic stays quiet: still live, not failed or reconnecting.
    tokio::time::sleep(Duration::from_millis(400)).await;
    assert_eq!(*live.status.borrow(), Status::Live { epoch: 0 });
    let subscriptions = fake.seen(|seen| seen.subscriptions.clone());
    assert_eq!(
        subscriptions,
        [serde_json::json!({
            "filters": [{ "topics": ["chosen"], "instanceFilter": [{ "channel_id": 7 }] }],
            "startFrom": { "position": "new" },
        })]
    );
    live.close().await;
}

/// How often the first-sample warning is logged by a live connection that behaves `first` and then goes quiet for
/// 20 s (on a paused clock, once the connection is up).
async fn first_sample_warnings(first: After) -> usize {
    let logged = Arc::new(Mutex::new(Vec::new()));
    let writer = Arc::clone(&logged);
    let _logging = tracing::subscriber::set_default(
        tracing_subscriber::fmt()
            .with_ansi(false)
            .with_writer(move || Captured(Arc::clone(&writer)))
            .finish(),
    );
    let fake = FakeDataHub::with(Behaviour {
        first,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), "chosen", 7);
    status_where(&mut live, |s| matches!(s, Status::Live { .. })).await;
    if matches!(first, After::Idle) {
        next_item(&mut live).await;
    }
    tokio::time::pause();
    tokio::time::sleep(Duration::from_secs(20)).await;
    assert_eq!(*live.status.borrow(), Status::Live { epoch: 0 });
    live.close().await;
    let text = String::from_utf8(logged.lock().expect("lock").clone()).expect("UTF-8");
    text.matches("No DataHub samples received for topic 'chosen' on channel 7.")
        .count()
}

#[tokio::test]
async fn a_topic_without_a_sample_for_ten_seconds_is_logged_once() {
    assert_eq!(first_sample_warnings(After::Quiet).await, 1);
}

#[tokio::test]
async fn a_topic_with_a_sample_in_time_is_not_logged() {
    assert_eq!(first_sample_warnings(After::Idle).await, 0);
}

/// Where the log of a test goes.
struct Captured(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for Captured {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().expect("lock").extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn the_topics_are_listed_through_a_digest_login() {
    let fake = FakeDataHub::start().await;
    let device = Device::new(&fake.camera()).expect("device");
    assert_eq!(
        device.datahub_topics().await.expect("topics"),
        [TOPIC, "other"]
    );
}

#[tokio::test]
async fn digest_is_used_when_basic_is_offered_too_and_a_rejected_login_stops_there() {
    let behaviour = Behaviour {
        offer_basic: true,
        ..Behaviour::default()
    };
    let fake = FakeDataHub::with(behaviour.clone()).await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    status_where(&mut live, |s| matches!(s, Status::Live { .. })).await;
    live.close().await;
    assert!(fake.seen(|seen| seen.authorizations.iter().all(|a| a.starts_with("Digest "))));

    let fake = FakeDataHub::with(behaviour).await;
    let mut live = open_datahub(&wrong_password(fake.camera()), TOPIC, 1);
    let Status::Failed(error) = status_where(&mut live, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::LoginRejected);
    // One request without a login, one with it, and no other try: not another Digest, never Basic.
    assert_eq!(fake.seen(|seen| seen.token_requests), 2);
    assert!(fake.seen(|seen| seen.authorizations.iter().all(|a| a.starts_with("Digest "))));
    assert_eq!(fake.seen(|seen| seen.upgrades), 0);
    assert!(!error.to_string().contains("not-the-password"));
    live.close().await;
}

#[tokio::test]
async fn a_camera_that_offers_only_basic_is_signed_in_to_over_plain_http() {
    let fake = FakeDataHub::with(Behaviour {
        basic_only: true,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    status_where(&mut live, |s| matches!(s, Status::Live { .. })).await;
    live.close().await;
}

#[tokio::test]
async fn a_self_signed_https_camera_gives_the_token_and_the_subscription_over_tls() {
    let fake = FakeDataHub::start_tls().await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    let sample = next_item(&mut live).await;
    assert_eq!(sample.payload.as_ref(), br#"{"connection":0}"#);
    // The token came over the same TLS host, behind the Digest login.
    assert_eq!(fake.seen(|seen| seen.token_requests), 2);
    assert_eq!(fake.seen(|seen| seen.upgrades), 1);
    live.close().await;
}

#[tokio::test]
async fn a_redirect_ends_the_subscription() {
    for redirect in [Redirect::Token, Redirect::Upgrade] {
        let fake = FakeDataHub::with(Behaviour {
            redirect: Some(redirect),
            ..Behaviour::default()
        })
        .await;
        let mut live = open_datahub(&fake.camera(), TOPIC, 1);
        let Status::Failed(error) =
            status_where(&mut live, |s| matches!(s, Status::Failed(_))).await
        else {
            unreachable!()
        };
        assert_eq!(error.kind, ErrorKind::InvalidTarget);
        assert_eq!(
            fake.seen(|seen| seen.upgrades),
            usize::from(redirect == Redirect::Upgrade)
        );
        live.close().await;
    }
}

#[tokio::test]
async fn a_protocol_failure_ends_the_subscription_without_a_retry() {
    let fake = FakeDataHub::with(Behaviour {
        first: After::Garbage,
        later: After::Garbage,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    let Status::Failed(error) = status_where(&mut live, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert!(!error.kind.retryable(), "{error}");
    tokio::time::sleep(Duration::from_millis(600)).await;
    assert_eq!(fake.seen(|seen| seen.upgrades), 1);
    live.close().await;
}

#[tokio::test]
async fn an_oversized_message_ends_the_subscription_without_a_retry() {
    let fake = FakeDataHub::with(Behaviour {
        first: After::Huge,
        later: After::Huge,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    let Status::Failed(error) = status_where(&mut live, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Unsupported, "{error}");
    tokio::time::sleep(Duration::from_millis(600)).await;
    assert_eq!(fake.seen(|seen| seen.upgrades), 1);
    live.close().await;
}

#[tokio::test]
async fn samples_dropped_before_the_subscription_is_confirmed_are_a_gap() {
    let fake = FakeDataHub::with(Behaviour {
        early_samples: 70,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    // The 64 newest are kept; the 6 oldest are reported lost ahead of them.
    let first = timeout(WAIT, live.messages.recv()).await;
    assert!(
        matches!(first, Ok(Some(Feed::Gap { lost: 6 }))),
        "{first:?}"
    );
    for n in 6..70 {
        let sample = next_item(&mut live).await;
        assert_eq!(sample.payload, format!(r#"{{"early":{n}}}"#).as_bytes());
    }
    assert_eq!(live.stats().feed_lost, 6);
    live.close().await;
}

#[tokio::test]
async fn a_lost_connection_is_made_again_with_the_next_epoch() {
    let fake = FakeDataHub::with(Behaviour {
        first: After::Close,
        ..Behaviour::default()
    })
    .await;
    let mut live = open_datahub(&fake.camera(), TOPIC, 1);
    status_where(&mut live, |s| *s == Status::Live { epoch: 1 }).await;
    let epochs: Vec<_> = [next_item(&mut live).await, next_item(&mut live).await]
        .iter()
        .map(|sample| (sample.epoch, sample.payload.clone()))
        .collect();
    assert_eq!(
        epochs,
        [
            (0, br#"{"connection":0}"#.as_slice().into()),
            (1, br#"{"connection":1}"#.as_slice().into())
        ]
    );
    assert_eq!(live.stats().reconnects, 1);
    live.close().await;
}
