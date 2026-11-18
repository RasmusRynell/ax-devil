//! MQTT analytics against an in-process camera and broker: the publisher lease, reconnects, refusals, overflow.

use crate::support;

use std::time::Duration;

use camera::{
    Broker, Camera, ErrorKind, Feed, Host, LiveMessages, Login, Message, Reconnect, Refusal,
    Secret, SourceKey, Status, open_mqtt,
};
use serde_json::Value;
use support::fake_broker::{self, Behaviour, FakeBroker};
use support::fake_vapix::FakeVapix;
use tokio::time::{sleep, timeout};

mod sources;

const KEY: &str = "com.axis.analytics_scene_description.v0.beta#1";
const FAST: Reconnect = Reconnect {
    first: Duration::from_millis(20),
    max: Duration::from_millis(100),
    silence: None,
};

fn broker_for(fake: &FakeBroker, password: &str) -> Broker {
    let mut broker = Broker::new(Host::parse("127.0.0.1").expect("host"));
    broker.port = fake.addr.port();
    broker.login = Some(Login {
        user: fake_broker::USER.into(),
        password: Secret::new(password),
    });
    broker
}

fn broker_without_login(fake: &FakeBroker) -> Broker {
    let mut broker = Broker::new(Host::parse("127.0.0.1").expect("host"));
    broker.port = fake.addr.port();
    broker
}

fn open(camera: &Camera, broker: &Broker) -> LiveMessages {
    open_mqtt(camera, broker, SourceKey(KEY.into()), FAST)
}

async fn status_where(live: &mut LiveMessages, matches: impl Fn(&Status) -> bool) -> Status {
    let status = timeout(
        Duration::from_secs(10),
        live.status.wait_for(|s| matches(s)),
    )
    .await
    .expect("the status did not come")
    .expect("status channel");
    status.clone()
}

async fn live(live: &mut LiveMessages, epoch: u32) {
    status_where(live, |s| *s == Status::Live { epoch }).await;
}

async fn next_item(live: &mut LiveMessages) -> Message {
    match timeout(Duration::from_secs(10), live.messages.recv()).await {
        Ok(Some(Feed::Item(message))) => message,
        other => panic!("expected a message, got {other:?}"),
    }
}

fn publishers(fake: &FakeVapix) -> Vec<Value> {
    fake.analytics(|a| a.publishers.clone())
}

#[tokio::test]
async fn a_publisher_is_created_for_the_subscription_and_everything_is_restored_on_close() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let before = vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone()));
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    live(&mut messages, 0).await;

    let during = publishers(&vapix);
    assert_eq!(during.len(), before.2.len() + 1);
    let ours = during.last().expect("publisher");
    let topic = ours["mqtt_topic"].as_str().expect("topic").to_owned();
    assert_eq!(ours["data_source_key"], KEY);
    vapix.analytics(|a| {
        assert_eq!(a.config["server"]["port"], broker.addr.port());
        assert_eq!(a.config["username"], fake_broker::USER);
        assert_eq!(a.config["password"], "unused");
        assert!(a.active);
    });

    broker.publish(&topic, b"<scene/>");
    let message = next_item(&mut messages).await;
    assert_eq!(
        (message.topic.as_str(), &message.payload[..], message.epoch),
        (topic.as_str(), &b"<scene/>"[..], 0)
    );

    messages.close().await;
    assert_eq!(
        vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone())),
        before
    );
}

#[tokio::test]
async fn a_matching_publisher_is_reused_and_left_in_place() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let camera = vapix.camera();
    // No broker password, so the configuration the first one leaves behind can still be restored by the second.
    // Dropped without close: the publisher it created stays, as the warning says.
    let mut leaked = open(&camera, &broker_without_login(&broker));
    live(&mut leaked, 0).await;
    drop(leaked);
    // The aborted task is dropped, and the camera freed, when the runtime next runs it.
    tokio::task::yield_now().await;
    let left = publishers(&vapix);
    assert_eq!(left.len(), 2);

    let mut messages = open(&camera, &broker_without_login(&broker));
    live(&mut messages, 0).await;
    assert_eq!(publishers(&vapix), left, "reused, not duplicated");
    vapix.analytics(|a| {
        let ours = a
            .configured
            .iter()
            .filter(|config| config["server"]["port"] == broker.addr.port());
        assert!(
            ours.clone().count() >= 2,
            "both subscriptions configured the camera"
        );
        for config in ours {
            let keys = config.as_object().expect("object");
            assert!(
                !keys.contains_key("username") && !keys.contains_key("password"),
                "{config}"
            );
        }
    });
    messages.close().await;
    assert_eq!(publishers(&vapix), left, "not ours to remove");
}

#[tokio::test]
async fn a_refused_subscription_is_terminal_and_the_camera_is_restored_first() {
    let vapix = FakeVapix::start().await;
    let broker = FakeBroker::with(Behaviour {
        refuse_subscriptions: true,
        ..Behaviour::default()
    })
    .await;
    let before = vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone()));
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    let status = status_where(&mut messages, |s| matches!(s, Status::Failed(_))).await;
    let Status::Failed(error) = status else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::BrokerRefused(Refusal::NotAuthorized));
    assert_eq!(
        vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone())),
        before
    );
    assert_eq!(broker.connects(), 1, "no retry");
}

#[tokio::test]
async fn refused_credentials_are_terminal_with_no_retry_and_the_password_is_never_shown() {
    let vapix = FakeVapix::start().await;
    let broker = FakeBroker::with(Behaviour {
        require_login: true,
        ..Behaviour::default()
    })
    .await;
    let before = vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone()));
    let wrong = "wrong-broker-password-5521";
    let broker_login = broker_for(&broker, wrong);
    let mut messages = open(&vapix.camera(), &broker_login);
    let status = status_where(&mut messages, |s| matches!(s, Status::Failed(_))).await;
    let Status::Failed(error) = &status else {
        unreachable!()
    };
    assert_eq!(
        error.kind,
        ErrorKind::BrokerRefused(Refusal::BadCredentials)
    );
    sleep(Duration::from_millis(300)).await;
    assert_eq!(broker.connects(), 1, "no retry");
    assert_eq!(
        vapix.analytics(|a| (a.config.clone(), a.active, a.publishers.clone())),
        before
    );
    let shown = format!("{status:?} {error} {broker_login:?} {messages:?}");
    assert!(!shown.contains(wrong), "{shown}");
}

#[tokio::test]
async fn a_dropped_broker_connection_comes_back_as_the_next_epoch() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    live(&mut messages, 0).await;
    let topic = publishers(&vapix).last().expect("publisher")["mqtt_topic"]
        .as_str()
        .expect("topic")
        .to_owned();

    broker.drop_connections();
    let Status::Reconnecting { error, .. } =
        status_where(&mut messages, |s| matches!(s, Status::Reconnecting { .. })).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Lost);
    live(&mut messages, 1).await;
    assert_eq!(messages.stats().reconnects, 1);
    broker.publish(&topic, b"again");
    assert_eq!(next_item(&mut messages).await.epoch, 1);
    assert_eq!(
        publishers(&vapix).len(),
        2,
        "the lease was kept across the reconnect"
    );
    messages.close().await;
}

#[tokio::test]
async fn a_burst_the_reader_cannot_keep_up_with_becomes_a_gap() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    live(&mut messages, 0).await;
    let topic = publishers(&vapix).last().expect("publisher")["mqtt_topic"]
        .as_str()
        .expect("topic")
        .to_owned();

    let sent = 600;
    for n in 0..sent {
        broker.publish(&topic, format!("{n}").as_bytes());
    }
    timeout(Duration::from_secs(10), async {
        while messages.stats().feed_lost == 0 {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("nothing overflowed");
    // Everything has arrived once the queue's worth plus the lost ones account for all of it.
    timeout(Duration::from_secs(10), async {
        while messages.stats().feed_lost + 256 < sent {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the burst did not finish");
    let lost = messages.stats().feed_lost;

    for _ in 0..256 {
        next_item(&mut messages).await;
    }
    broker.publish(&topic, b"after");
    let Some(Feed::Gap { lost: told }) = messages.messages.recv().await else {
        panic!("the loss was not announced");
    };
    assert_eq!(told, lost);
    assert_eq!(&next_item(&mut messages).await.payload[..], b"after");
    messages.close().await;
}

/// The camera's MQTT client settings, whether it is active, and its publishers.
fn camera_state(fake: &FakeVapix) -> (Value, bool, Vec<Value>) {
    fake.analytics(|a| (a.config.clone(), a.active, a.publishers.clone()))
}

#[tokio::test]
async fn a_second_subscription_to_a_held_camera_is_busy_and_changes_nothing() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let camera = vapix.camera();
    let before = camera_state(&vapix);
    let mut first = open(&camera, &broker_for(&broker, "unused"));
    live(&mut first, 0).await;
    let during = camera_state(&vapix);

    let mut second = open(&camera, &broker_for(&broker, "unused"));
    let Status::Failed(error) = status_where(&mut second, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Busy);
    assert_eq!(
        camera_state(&vapix),
        during,
        "the second one left the camera alone"
    );
    assert_eq!(broker.connects(), 1, "and never reached the broker");
    let topic = publishers(&vapix).last().expect("publisher")["mqtt_topic"]
        .as_str()
        .expect("topic")
        .to_owned();
    broker.publish(&topic, b"still here");
    assert_eq!(&next_item(&mut first).await.payload[..], b"still here");

    first.close().await;
    assert_eq!(camera_state(&vapix), before);
    // Closed, so the camera is free again, and the new subscriber has an id of its own.
    let mut third = open(&camera, &broker_for(&broker, "unused"));
    live(&mut third, 0).await;
    third.close().await;
    let ids = broker.client_ids();
    assert_eq!(ids.len(), 2);
    assert_ne!(ids[0], ids[1]);
    assert_eq!(camera_state(&vapix), before);
}

#[tokio::test]
async fn a_release_that_fails_is_reported_instead_of_a_plain_close() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    vapix.analytics(|a| a.fail_deletes = true);
    let before = camera_state(&vapix);
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    live(&mut messages, 0).await;
    let id = publishers(&vapix).last().expect("publisher")["id"]
        .as_str()
        .expect("id")
        .to_owned();
    let status = messages.status.clone();
    messages.close().await;

    let Status::Failed(error) = status.borrow().clone() else {
        panic!("a failed release is not Closed: {:?}", status.borrow());
    };
    assert!(error.detail().contains("was not restored"), "{error}");
    assert!(
        error.detail().contains(&id),
        "it says which publisher may remain: {error}"
    );
    let after = camera_state(&vapix);
    assert_eq!(
        (&after.0, after.1),
        (&before.0, before.1),
        "the client settings were still restored"
    );
    assert_eq!(after.2.len(), before.2.len() + 1);
}

#[tokio::test]
async fn closing_during_acquisition_waits_for_it_and_leaves_the_camera_clean() {
    let vapix = FakeVapix::with(support::fake_vapix::Behaviour {
        slow_create: Duration::from_millis(300),
        ..support::fake_vapix::Behaviour::default()
    })
    .await;
    let broker = FakeBroker::start().await;
    let before = camera_state(&vapix);
    let messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    // Close while the camera is applying the new publisher, which it does whether or not anyone waits.
    timeout(Duration::from_secs(5), async {
        while !vapix
            .seen()
            .iter()
            .any(|seen| seen.method == "POST" && seen.target.ends_with("/publishers"))
        {
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("the publisher was never requested");
    messages.close().await;
    assert_eq!(camera_state(&vapix), before);
}

#[tokio::test]
async fn publications_above_10_kib_arrive_and_ones_above_the_limit_end_the_subscription() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    let before = camera_state(&vapix);
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    live(&mut messages, 0).await;
    let topic = publishers(&vapix).last().expect("publisher")["mqtt_topic"]
        .as_str()
        .expect("topic")
        .to_owned();

    broker.publish(&topic, &vec![b'x'; 20 * 1024]);
    assert_eq!(next_item(&mut messages).await.payload.len(), 20 * 1024);

    broker.publish(&topic, &vec![b'x'; 5 * 1024 * 1024]);
    let Status::Failed(error) =
        status_where(&mut messages, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Unsupported);
    assert_eq!(broker.connects(), 1, "no reconnect loop");
    assert_eq!(camera_state(&vapix), before);
}

#[tokio::test]
async fn a_camera_with_an_authenticated_mqtt_client_is_refused_before_anything_changes() {
    let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
    // The camera reports a configured password masked, so it could never be put back.
    vapix.analytics(|a| a.config["password"] = "*****".into());
    let before = camera_state(&vapix);
    let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
    let Status::Failed(error) =
        status_where(&mut messages, |s| matches!(s, Status::Failed(_))).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::Unrestorable);
    assert!(!error.kind.retryable());
    assert!(error.detail().contains("cannot be read back"), "{error}");
    assert_eq!(camera_state(&vapix), before);
    assert_eq!(broker.connects(), 0);
}

#[tokio::test]
async fn a_publisher_whose_creation_timed_out_is_removed_once_the_camera_has_applied_it() {
    let vapix = FakeVapix::with(support::fake_vapix::Behaviour {
        slow_create: Duration::from_millis(150),
        ..support::fake_vapix::Behaviour::default()
    })
    .await;
    let broker = FakeBroker::start().await;
    let before = camera_state(&vapix);
    let mut camera = vapix.camera();
    camera.timeout = Duration::from_millis(100);
    let mut messages = open(&camera, &broker_for(&broker, "unused"));
    // The creation outlasts the client's patience, then the camera applies it anyway: wait for that to be seen, so the
    // check below cannot pass for a creation that never happened.
    timeout(Duration::from_secs(10), async {
        while publishers(&vapix).len() == before.2.len() {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the camera never applied the creation");
    let Status::Reconnecting { error, .. } =
        status_where(&mut messages, |s| matches!(s, Status::Reconnecting { .. })).await
    else {
        unreachable!()
    };
    assert_eq!(error.kind, ErrorKind::TimedOut);
    // Closing finishes what is in flight and gives the camera back before it returns.
    let status = messages.status.clone();
    messages.close().await;
    assert_eq!(*status.borrow(), Status::Closed);
    assert_eq!(camera_state(&vapix), before);
}

#[tokio::test]
async fn cameras_on_one_host_with_different_ports_get_their_own_topic_and_client_id() {
    let (first, second) = (FakeVapix::start().await, FakeVapix::start().await);
    let broker = FakeBroker::start().await;
    let mut one = open(&first.camera(), &broker_for(&broker, "unused"));
    let mut two = open(&second.camera(), &broker_for(&broker, "unused"));
    live(&mut one, 0).await;
    live(&mut two, 0).await;
    let topic =
        |fake: &FakeVapix| publishers(fake).last().expect("publisher")["mqtt_topic"].clone();
    let client = |fake: &FakeVapix| fake.analytics(|a| a.config["clientId"].clone());
    assert_ne!(topic(&first), topic(&second));
    assert_ne!(client(&first), client(&second));
    one.close().await;
    two.close().await;
}

#[tokio::test]
async fn a_leftover_of_this_crate_or_the_python_client_is_taken_over_and_left_inactive() {
    for client_id in ["ax-devil-0123456789abcdef", "ax-devil/temp/1caf284c"] {
        let (vapix, broker) = (FakeVapix::start().await, FakeBroker::start().await);
        // What an interrupted run leaves: its client id, a password the camera will not reveal, and an active client.
        vapix.analytics(|a| {
            a.config["clientId"] = client_id.into();
            a.config["password"] = "*****".into();
            a.active = true;
        });
        let publishers_before = publishers(&vapix);
        let mut messages = open(&vapix.camera(), &broker_for(&broker, "unused"));
        live(&mut messages, 0).await;
        let status = messages.status.clone();
        messages.close().await;
        assert_eq!(*status.borrow(), Status::Closed, "{client_id}");
        assert!(!vapix.analytics(|a| a.active), "{client_id} left inactive");
        let config = vapix.analytics(|a| a.config.clone());
        assert!(
            config.get("username").is_none() && config.get("password").is_none(),
            "{client_id}: the broker login is cleared: {config}"
        );
        assert_eq!(publishers(&vapix), publishers_before, "{client_id}");
    }
}
