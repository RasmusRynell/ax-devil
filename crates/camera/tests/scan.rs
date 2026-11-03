//! The network scan against in-process cameras on ephemeral ports: what is sent to whom, rate limits, and stopping.

mod support;

use std::time::Duration;

use camera::{
    Found, Login, ScanLoad, ScanPorts, Scheme, Secret, SignIn, Targets, Web, check, scan,
};
use support::fake_vapix::{Behaviour, FakeVapix, PASSWORD, USER};
use tokio::time::{sleep, timeout};

const WAIT: Duration = Duration::from_secs(20);

fn login(user: &str, password: &str) -> Login {
    Login {
        user: user.into(),
        password: Secret::new(password),
    }
}

fn ports(fakes: &[&FakeVapix]) -> ScanPorts {
    ScanPorts::new(fakes.iter().map(|fake| Web {
        scheme: Scheme::Http,
        port: fake.addr.port(),
    }))
}

/// Scans 127.0.0.1 and returns every camera found by the time the scan ends.
async fn scan_loopback(logins: Vec<Login>, ports: ScanPorts) -> Vec<Found> {
    let targets = Targets::parse("127.0.0.1").expect("targets");
    let mut found = scan(targets, logins, ScanLoad::Normal, ports);
    let mut all = Vec::new();
    while let Some(camera) = timeout(WAIT, found.recv()).await.expect("the scan ends") {
        all.push(camera);
    }
    all
}

#[tokio::test]
async fn only_a_camera_is_signed_in_to_with_the_login_that_works() {
    let not_a_camera = FakeVapix::with(Behaviour {
        brand: "ACME",
        login_for_info: true,
        ..Behaviour::default()
    })
    .await;
    let camera = FakeVapix::start().await;
    let logins = vec![
        login("", "skipped"),
        login(USER, "wrong"),
        login(USER, PASSWORD),
    ];

    let found = scan_loopback(logins, ports(&[&not_a_camera, &camera])).await;

    assert_eq!(found.len(), 1, "one device per address");
    let found = &found[0];
    assert_eq!(found.web.port, camera.addr.port());
    assert_eq!(found.identity.model, "AXIS Q1656");
    assert_eq!(found.uptime, Some(Duration::from_secs(93784)));
    assert_eq!(
        found.sign_in,
        SignIn::SignedIn {
            login_index: 2,
            architecture: "aarch64".into(),
            soc: "Ambarella CV25".into()
        }
    );
    assert_eq!(
        not_a_camera.logins_seen(),
        0,
        "a host that is not a camera is sent no login"
    );
    assert!(
        camera
            .seen()
            .iter()
            .all(|seen| !seen.target.contains(PASSWORD)),
        "no password in a URL"
    );
    assert!(!format!("{found:?}").contains(PASSWORD));
}

#[tokio::test]
async fn a_camera_turning_logins_away_for_now_is_asked_again_after_a_wait() {
    let camera = FakeVapix::with(Behaviour {
        throttle_logins: 1,
        ..Behaviour::default()
    })
    .await;
    let started = tokio::time::Instant::now();

    let found = scan_loopback(vec![login(USER, PASSWORD)], ports(&[&camera])).await;

    assert!(matches!(
        found[0].sign_in,
        SignIn::SignedIn { login_index: 0, .. }
    ));
    assert!(started.elapsed() >= Duration::from_secs(1), "waited 1 s");
    assert_eq!(camera.logins_seen(), 2, "the 429, then the retry");
}

#[tokio::test]
async fn dropping_the_receiver_sends_no_further_login() {
    let camera = FakeVapix::with(Behaviour {
        delay: Duration::from_millis(400),
        ..Behaviour::default()
    })
    .await;
    let logins = vec![
        login(USER, "wrong"),
        login(USER, "wrong too"),
        login(USER, PASSWORD),
    ];
    let found = scan(
        Targets::parse("127.0.0.1").expect("targets"),
        logins,
        ScanLoad::Startup,
        ports(&[&camera]),
    );
    timeout(WAIT, async {
        while camera.logins_seen() == 0 {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the first login");

    drop(found);
    sleep(Duration::from_millis(1500)).await;

    assert_eq!(camera.logins_seen(), 1, "the second login was never sent");
}

#[tokio::test]
async fn checking_one_login_on_one_host_tells_if_it_is_accepted_and_sends_nothing_to_a_non_camera()
{
    let camera = FakeVapix::start().await;
    let not_a_camera = FakeVapix::with(Behaviour {
        brand: "ACME",
        login_for_info: true,
        ..Behaviour::default()
    })
    .await;
    let addr = std::net::Ipv4Addr::LOCALHOST;
    let web = |fake: &FakeVapix| Web {
        scheme: Scheme::Http,
        port: fake.addr.port(),
    };

    let accepted = check(addr, web(&camera), login(USER, PASSWORD)).await;
    let refused = check(addr, web(&camera), login(USER, "wrong")).await;
    let elsewhere = check(addr, web(&not_a_camera), login(USER, PASSWORD)).await;

    assert!(matches!(
        accepted.map(|found| found.sign_in),
        Some(SignIn::SignedIn { .. })
    ));
    assert_eq!(refused.map(|found| found.sign_in), Some(SignIn::Refused));
    assert!(elsewhere.is_none());
    assert_eq!(not_a_camera.logins_seen(), 0);
}
