//! RTSP through the camera's web server: the HTTP(S) tunnel against an in-process fake, end to end through retina.

use crate::rtsp::{WAIT, next_feed, next_unit, request, status_where};
use crate::support;

use std::time::Duration;

use camera::{ErrorKind, Feed, RtspRoute, Secret, Status, StreamRequest, Url, open_stream};
use support::fake_rtsp::Behaviour;
use support::fake_tunnel::{FakeTunnel, Options};
use support::fake_vapix::{PASSWORD, USER};
use tokio::time::{sleep, timeout};

fn web_request(video: bool, metadata: bool) -> StreamRequest {
    StreamRequest {
        route: RtspRoute::WebApi,
        ..request(video, metadata)
    }
}

#[tokio::test]
async fn video_and_metadata_arrive_through_the_tunnel_over_http_and_https() {
    for tls in [false, true] {
        let fake = FakeTunnel::start(Options {
            tls,
            ..Options::default()
        })
        .await;
        let mut stream = open_stream(&fake.camera(), web_request(true, true));
        status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
        let unit = next_unit(&mut stream).await;
        assert!(unit.keyframe && unit.epoch == 0, "tls {tls}");
        assert!(matches!(
            next_feed(&mut stream.metadata).await,
            Feed::Item(_)
        ));

        // The GET is challenged and asked again with the next nonce count, then the POST pairs by the cookie.
        let seen = fake.seen();
        let methods: Vec<_> = seen.iter().map(|r| r.method.as_str()).collect();
        assert_eq!(methods, ["GET", "GET", "POST"], "tls {tls}");
        assert!(
            seen.iter()
                .all(|r| r.cookie == seen[0].cookie && r.cookie.len() == 16)
        );
        assert!(seen[0].authorization.is_none() && seen[2].authorization.is_some());
        assert!(
            seen.iter()
                .all(|r| r.uri.starts_with("/axis-media/media.amp?camera=1"))
        );
        assert_eq!(
            fake.bad_base64(),
            0,
            "every POST message is its own base64 run"
        );
        let rtsp = fake.rtsp.seen();
        assert!(rtsp.iter().any(|r| r.method == "PLAY"), "{rtsp:?}");
        for request in seen
            .iter()
            .map(|r| &r.uri)
            .chain(rtsp.iter().map(|r| &r.uri))
        {
            assert!(
                !request.contains(PASSWORD) && !request.contains('@'),
                "{request}"
            );
        }
        stream.close().await;
    }
}

#[tokio::test]
async fn a_wrong_login_fails_the_tunnel_at_once_without_a_retry() {
    let fake = FakeTunnel::start(Options::default()).await;
    let mut camera = fake.camera();
    camera.login.as_mut().expect("a login").password = Secret::new("wrong-pass-9999");
    let mut stream = open_stream(&camera, web_request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    assert!(matches!(&status, Status::Failed(e) if e.kind == ErrorKind::LoginRejected));
    assert!(!format!("{status:?}").contains("wrong-pass"));
    sleep(Duration::from_millis(300)).await;
    let methods: Vec<_> = fake.seen().into_iter().map(|r| r.method).collect();
    assert_eq!(
        methods,
        ["GET", "GET"],
        "the challenge, one answer, no POST, no retry"
    );
    assert_eq!(fake.rtsp.connections(), 0);

    camera.login = None;
    let mut stream = open_stream(&camera, web_request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    assert!(matches!(&status, Status::Failed(e) if e.kind == ErrorKind::LoginRequired));
}

#[tokio::test]
async fn a_challenge_offering_auth_int_is_answered_with_auth_because_the_post_streams() {
    let fake = FakeTunnel::start(Options {
        qop: Some("auth,auth-int"),
        ..Options::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), web_request(true, false));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    let seen = fake.seen();
    let used: Vec<_> = seen
        .iter()
        .filter_map(|r| r.authorization.as_deref())
        .collect();
    assert_eq!(used.len(), 2, "the GET and the POST were both signed");
    assert!(
        used.iter().all(|header| !header.contains("auth-int")),
        "{used:?}"
    );
    stream.close().await;

    // A camera that accepts nothing but auth-int cannot be answered: the stream fails for good, before any POST.
    let fake = FakeTunnel::start(Options {
        qop: Some("auth-int"),
        ..Options::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), web_request(true, false));
    let status = status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await;
    assert!(matches!(&status, Status::Failed(e) if e.kind == ErrorKind::Unsupported));
    assert!(fake.seen().iter().all(|r| r.method == "GET"));
}

#[tokio::test]
async fn a_tunnel_the_camera_ends_after_its_post_budget_comes_back_as_the_next_epoch() {
    // 3 s of session timeout means a keepalive every 1.5 s: it is the request that overruns the budget, after the
    // setup (about 2 kB) has gone through.
    let fake = FakeTunnel::start(Options {
        post_budget: Some(3500),
        rtsp: Behaviour {
            session_secs: 3,
            ..Behaviour::default()
        },
        ..Options::default()
    })
    .await;
    let mut stream = open_stream(&fake.camera(), web_request(true, false));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    assert_eq!(next_unit(&mut stream).await.epoch, 0);
    let reconnecting =
        status_where(&mut stream, |s| matches!(s, Status::Reconnecting { .. })).await;
    assert!(
        matches!(&reconnecting, Status::Reconnecting { error, .. } if error.kind == ErrorKind::Lost)
    );
    status_where(&mut stream, |s| *s == Status::Live { epoch: 1 }).await;
    // The frames of the first tunnel still queued come first; one deadline bounds the drain, so frames wrongly
    // tagged epoch 0 for ever fail the test instead of hanging it.
    let drained = timeout(WAIT, async {
        while next_unit(&mut stream).await.epoch == 0 {}
    })
    .await;
    assert!(drained.is_ok(), "no frame of epoch 1 within {WAIT:?}");
    assert_eq!(stream.stats().reconnects, 1);
    let posts: Vec<_> = fake
        .seen()
        .into_iter()
        .filter(|r| r.method == "POST")
        .collect();
    assert_eq!(posts.len(), 2);
    assert_ne!(
        posts[0].cookie, posts[1].cookie,
        "a new tunnel, a new cookie"
    );
    stream.close().await;
}

#[tokio::test]
async fn close_tears_the_session_down_through_the_tunnel_and_closes_both_connections() {
    let fake = FakeTunnel::start(Options::default()).await;
    let mut stream = open_stream(&fake.camera(), web_request(true, false));
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    stream.close().await;
    assert!(
        fake.rtsp.seen().iter().any(|r| r.method == "TEARDOWN"),
        "the TEARDOWN reached the camera before close returned"
    );
    timeout(Duration::from_secs(5), async {
        while fake.closed() == 0 {
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the camera saw the GET and the POST close");
}

#[tokio::test]
async fn a_custom_http_url_logs_in_with_its_own_login() {
    let fake = FakeTunnel::start(Options::default()).await;
    let mut camera = fake.camera();
    camera.login = None;
    let mut request = request(true, false);
    request.url = Url::parse(&format!(
        "http://{USER}:{PASSWORD}@127.0.0.1:{}/axis-media/media.amp?camera=1",
        fake.addr.port()
    ))
    .ok();
    let mut stream = open_stream(&camera, request);
    status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await;
    assert!(!format!("{stream:?}").contains(PASSWORD));
    stream.close().await;
}

/// What a stream says about a failure is shown to people and written to logs, so it holds nothing the stream URL or
/// the camera said: not the login, not a query token, not a path parameter the camera echoes back as a content type.
/// Retina quotes all of that in its own messages, which the app's logging never writes (`logging`'s tests).
#[tokio::test]
async fn what_a_stream_says_of_a_failure_holds_nothing_the_url_or_the_camera_said() {
    const SECRETS: [&str; 5] = ["QUERYSECRET", "PATHSECRET", "URLPASS", "token", "session"];
    // (the camera's Content-Type for DESCRIBE, the query and path of the URL, whether the stream fails)
    for (content_type, path, query, fails) in [
        (
            "application/json",
            "/axis-media/media.amp",
            "camera=1&token=QUERYSECRET",
            true,
        ),
        ("{target}", "/s;session=PATHSECRET", "camera=1", true),
        // No content type: retina warns with the URL and goes on, so the stream plays.
        (
            "",
            "/axis-media/media.amp",
            "camera=1&token=QUERYSECRET",
            false,
        ),
    ] {
        let (_logging, logged) = support::log::capture();
        let fake = FakeTunnel::start(Options {
            rtsp: Behaviour {
                describe_type: content_type,
                ..Behaviour::default()
            },
            ..Options::default()
        })
        .await;
        let mut camera = fake.camera();
        camera.login = None;
        let mut request = request(true, false);
        // The login is the fake's own, so the URL carries it under a name this test can look for.
        let url = format!(
            "http://{USER}:{PASSWORD}@127.0.0.1:{}{path}?{query}",
            fake.addr.port()
        );
        request.url = Url::parse(&url).ok();
        let mut stream = open_stream(&camera, request);
        let status = if fails {
            status_where(&mut stream, |s| matches!(s, Status::Failed(_))).await
        } else {
            status_where(&mut stream, |s| *s == Status::Live { epoch: 0 }).await
        };
        let shown = format!("{status:?} {}", logged.text());
        let shown = match &status {
            Status::Failed(error) => format!("{shown} {error} {error:?}"),
            _ => shown,
        };
        if fails {
            assert!(shown.contains("unexpected content type"), "{shown}");
        }
        for secret in SECRETS.into_iter().chain([PASSWORD, "media.amp"]) {
            assert!(!shown.contains(secret), "{secret} in {shown}");
        }
        stream.close().await;
    }
}
