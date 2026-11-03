//! DataHub against an in-process camera: login and redirect rules, the exact subscription, terminal failures and
//! reconnects.

mod support;

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
