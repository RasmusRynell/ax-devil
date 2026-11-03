//! The device API against an in-process fake camera: sign-in, what is sent without a login, typed failures.

mod support;

use std::time::Duration;

use camera::{
    Camera, Device, ErrorKind, Head, Host, Login, Resolution, Scheme, Secret, SourceKey, Web,
};
use support::digest::{Algorithm, Exchange};
use support::fake_vapix::{Behaviour, FakeVapix, JPEG, PASSWORD};

fn device(camera: &Camera) -> Device {
    Device::new(camera).expect("device")
}

#[test]
fn the_fake_agrees_with_the_rfc_2617_digest_example() {
    let exchange = Exchange {
        algorithm: Algorithm::Md5,
        user: "Mufasa",
        realm: "testrealm@host.com",
        password: "Circle Of Life",
        nonce: "dcd98b7102dd2f0e8b11d0f600bfb0c093",
        nc: "00000001",
        cnonce: "0a4f113b",
        qop: "auth",
        method: "GET",
        uri: "/dir/index.html",
    };
    assert_eq!(exchange.response(), "6629fae49393a05397450978507c4ef1");
}

#[tokio::test]
async fn sign_in_answers_a_digest_challenge_with_either_hash() {
    for algorithm in [Algorithm::Md5, Algorithm::Sha256] {
        let fake = FakeVapix::with(Behaviour {
            algorithm,
            ..Behaviour::default()
        })
        .await;
        let identity = device(&fake.camera()).sign_in().await.expect("sign in");
        assert_eq!(identity.model, "AXIS Q1656");
        assert_eq!(identity.serial, "ACCC8E000001");
        assert_eq!(identity.firmware, "11.11.1");
        let seen = fake.seen();
        assert_eq!(seen.len(), 2, "one challenge, one answer");
        assert!(seen[0].authorization.is_none());
        assert!(seen[1].authorization.is_some());
        assert!(
            !seen[1].target.contains(PASSWORD),
            "the password is never in a URL"
        );
    }
}

#[tokio::test]
async fn a_wrong_password_is_rejected_once_and_leaks_nothing() {
    let fake = FakeVapix::start().await;
    let mut camera = fake.camera();
    camera.login.as_mut().expect("login").password = camera::Secret::new("not-the-password");
    let error = device(&camera).sign_in().await.expect_err("rejected");
    assert_eq!(error.kind, ErrorKind::LoginRejected);
    assert!(!error.kind.retryable());
    assert_eq!(fake.seen().len(), 2, "no second attempt");
    let shown = format!("{error} {error:?} {camera:?}");
    assert!(
        !shown.contains("not-the-password") && !shown.contains(PASSWORD),
        "{shown}"
    );
}

#[tokio::test]
async fn no_login_is_login_required() {
    let fake = FakeVapix::start().await;
    let device = device(&fake.camera_without_login());
    assert_eq!(
        device.sign_in().await.expect_err("no login").kind,
        ErrorKind::LoginRequired
    );
    assert!(
        fake.seen().is_empty(),
        "sign_in without a login sends nothing"
    );
    assert_eq!(
        device.snapshot(Head::FIRST).await.expect_err("401").kind,
        ErrorKind::LoginRequired
    );
}

#[tokio::test]
async fn identify_never_sends_the_login() {
    let fake = FakeVapix::start().await;
    let identity = device(&fake.camera()).identify().await.expect("identify");
    assert_eq!(identity.product_type, "Box Camera");
    assert!(
        fake.seen()
            .iter()
            .all(|request| request.authorization.is_none())
    );

    let guarded = FakeVapix::with(Behaviour {
        login_for_info: true,
        ..Behaviour::default()
    })
    .await;
    let error = device(&guarded.camera())
        .identify()
        .await
        .expect_err("wants a login");
    assert_eq!(error.kind, ErrorKind::LoginRequired);
    assert_eq!(guarded.seen().len(), 1);
}

#[tokio::test]
async fn a_device_that_is_not_an_axis_camera_is_not_axis() {
    let other_brand = FakeVapix::with(Behaviour {
        brand: "ACME",
        ..Behaviour::default()
    })
    .await;
    assert_eq!(
        device(&other_brand.camera())
            .identify()
            .await
            .expect_err("brand")
            .kind,
        ErrorKind::NotAxis
    );
    let plain_server = FakeVapix::with(Behaviour {
        not_found: true,
        ..Behaviour::default()
    })
    .await;
    assert_eq!(
        device(&plain_server.camera())
            .identify()
            .await
            .expect_err("404")
            .kind,
        ErrorKind::NotAxis
    );
}

#[tokio::test]
async fn redirects_are_not_followed() {
    let elsewhere = FakeVapix::start().await;
    let url = format!("http://{}/axis-cgi/basicdeviceinfo.cgi", elsewhere.addr);
    let redirecting = FakeVapix::with(Behaviour {
        redirect_to: Some(url),
        ..Behaviour::default()
    })
    .await;
    let error = device(&redirecting.camera())
        .identify()
        .await
        .expect_err("redirect");
    assert_eq!(error.kind, ErrorKind::InvalidTarget);
    assert!(elsewhere.seen().is_empty());
}

#[tokio::test]
async fn snapshot_is_the_jpeg_of_the_head() {
    let fake = FakeVapix::start().await;
    let bytes = device(&fake.camera())
        .snapshot(Head::new(2).expect("head"))
        .await
        .expect("snapshot");
    assert_eq!(&bytes[..], JPEG);
    assert!(
        fake.seen()
            .last()
            .expect("request")
            .target
            .ends_with("?camera=2")
    );
}

#[tokio::test]
async fn a_snapshot_is_abandoned_once_it_is_longer_than_the_limit() {
    let fake = FakeVapix::with(Behaviour {
        endless_snapshot: true,
        ..Behaviour::default()
    })
    .await;
    // The body never ends, so only stopping at the limit gives this error instead of a timeout.
    let error = device(&fake.camera())
        .snapshot(Head::FIRST)
        .await
        .expect_err("too long");
    assert_eq!(error.kind, ErrorKind::Malformed, "{error}");
}

#[tokio::test]
async fn an_ordinary_answer_is_abandoned_once_it_is_longer_than_the_limit() {
    let fake = FakeVapix::with(Behaviour {
        endless_info: true,
        ..Behaviour::default()
    })
    .await;
    let error = device(&fake.camera())
        .identify()
        .await
        .expect_err("too long");
    assert_eq!(error.kind, ErrorKind::Malformed, "{error}");
}

#[tokio::test]
async fn resolutions_and_analytics_sources_parse() {
    let fake = FakeVapix::start().await;
    let device = device(&fake.camera());
    let size = |width, height| Resolution { width, height };
    assert_eq!(
        device.resolutions(Head::FIRST).await.expect("resolutions"),
        [size(1920, 1080), size(1280, 720), size(640, 360)]
    );
    assert_eq!(
        device
            .resolutions(Head::new(2).expect("head"))
            .await
            .expect("resolutions"),
        [size(800, 600)]
    );
    let sources = device.analytics_sources().await.expect("sources");
    assert_eq!(
        sources[0],
        SourceKey("com.axis.analytics_scene_description.v0.beta#1".into())
    );
    assert_eq!(sources.len(), 2);
}

#[tokio::test]
async fn unreachable_and_slow_devices_are_retryable() {
    let fake = FakeVapix::start().await;
    let camera = fake.camera();
    drop(fake);
    let error = device(&camera).identify().await.expect_err("closed port");
    assert_eq!(error.kind, ErrorKind::Unreachable);
    assert!(error.kind.retryable());

    let slow = FakeVapix::with(Behaviour {
        delay: Duration::from_secs(2),
        ..Behaviour::default()
    })
    .await;
    let mut camera = slow.camera();
    camera.timeout = Duration::from_millis(200);
    let error = device(&camera).identify().await.expect_err("timeout");
    assert_eq!(error.kind, ErrorKind::TimedOut);
}

#[tokio::test]
async fn basic_is_sent_over_https_with_an_unverified_certificate() {
    let tls = support::tls_server::BasicOverTls::start().await;
    let mut camera = Camera::new(Host::parse("127.0.0.1").expect("host"));
    camera.web = Web {
        scheme: Scheme::Https,
        port: tls.addr.port(),
    };
    camera.login = Some(Login {
        user: "root".into(),
        password: Secret::new(PASSWORD),
    });
    let identity = device(&camera).sign_in().await.expect("sign in over HTTPS");
    assert_eq!(identity.model, "AXIS M3086");
    // base64("root:pass-4711")
    assert_eq!(
        *tls.authorizations.lock().expect("lock"),
        ["Basic cm9vdDpwYXNzLTQ3MTE="]
    );
}
