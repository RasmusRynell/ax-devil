//! The routes behind the live form: the questions it asks a camera (stream sizes, analytics sources, DataHub topics).
//! The network scan's routes are in [`super::network`].
//!
//! A question connects to the camera with the form's connection, so its `$NAME` references expand as a stream item's
//! do for the person at this machine, and stay literal for anyone else. The question is the handler's future, so a client that drops the request ends the connection. A question about another stream of a camera (`like`) authenticates with
//! that stream's saved login on the backend, for the person at this machine only and under the rule of the intake's
//! `copy_login`. Nothing here logs or returns a login.

use crate::{
    AppState, Failure, access::Principal, camera_questions, live_sources, session::live::defaults,
};
use api_types::{
    Audience, ErrorParams, ErrorWire,
    live::{
        LiveConnection, LiveReason, LiveResolutions, LiveTopics, NewLiveOverlayWire, NewLiveWire,
        ResolutionsRequest, SourcesAnswer, SourcesOverlay, SourcesRequest,
    },
    project,
};
use axum::{Json, extract::State, http::StatusCode};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use workspace::{Content, Environment, ResolvedCamera, Secret, Source, intake};

/// A camera that did not answer, or answered no: its reason is a code the client words, never the device's text.
fn camera_failed(reason: LiveReason) -> Failure {
    let error = ErrorWire {
        code: ErrorCode::CameraFailed,
        params: ErrorParams {
            camera: Some(reason),
            ..ErrorParams::default()
        },
        reason: None,
        location: None,
    };
    (StatusCode::BAD_GATEWAY, Json(error))
}

/// The camera `connection` names, with the settings' defaults for what it leaves out and its references expanded.
/// For the person at this machine, a blank password is the configured one, and a blank login of a connection `like` a
/// saved stream is that stream's, when the camera is the same one.
fn camera(
    state: &AppState,
    principal: Principal,
    connection: LiveConnection,
) -> Result<ResolvedCamera, Failure> {
    let settings = state.settings.state();
    let handlers = state.plugins.handlers();
    // The configured passwords are for Full access: another principal names its own.
    let full_access = principal.full_access();
    let mut defaults = defaults(&settings, full_access);
    if !full_access {
        defaults = defaults.without_secrets();
    }
    let like = connection.like;
    let mut new = intake::NewLive::from(NewLiveWire {
        host: connection.host,
        username: connection.username,
        password: connection.password,
        protocol: connection.protocol,
        port: connection.port,
        head: connection.head,
        overlay: Some(NewLiveOverlayWire::None),
        ..NewLiveWire::default()
    });
    let invalid = |error: &UserError| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(project(error, Audience::HOST)),
        )
    };
    if full_access
        && let Some(held) = like.and_then(|item| state.sessions.find_item(item))
        && let Source::Live(saved) = &held.item.recipe.source
    {
        new.copy_login(saved, &defaults, intake::LoginUse::Discovery)
            .map_err(|error| invalid(&error))?;
    }
    // A login sent as empty was cleared on purpose, as when an edited stream is saved: no configured one replaces it.
    if new.username.as_deref() == Some("") {
        defaults.username.clear();
    }
    if new
        .password
        .as_ref()
        .is_some_and(|entered| entered.expose().is_empty())
    {
        defaults.password = Secret::new("");
    }
    let item = intake::live(new, &defaults, &handlers).map_err(|error| invalid(&error))?;
    // A reference reads this machine's environment only with Full access; any other text,
    // defaults included, stays literal.
    let env = if full_access {
        Environment::of_process()
    } else {
        Environment::literal()
    };
    match item
        .recipe
        .resolve(&item.id, &item.label, &handlers, &env, &CancelToken::new())
    {
        Ok(Content::LiveVideo(content)) => Ok(content.camera),
        Ok(_) => unreachable!("a live recipe resolves to live video"),
        Err(Interrupt::Failed(error)) => Err(invalid(&error)),
        Err(Interrupt::Cancelled) => unreachable!("nothing cancels this token"),
    }
}

/// The stream sizes the head of the connection's camera offers.
pub(super) async fn resolutions(
    State(state): State<AppState>,
    principal: Principal,
    Json(request): Json<ResolutionsRequest>,
) -> Result<Json<LiveResolutions>, Failure> {
    let camera = camera(&state, principal, request.connection)?;
    let timeout = live_sources::timeout(&state.settings.state());
    camera_questions::resolutions(&camera, timeout)
        .await
        .map(Json)
        .map_err(camera_failed)
}

/// The analytics data sources or the DataHub topics of the connection's camera.
pub(super) async fn sources(
    State(state): State<AppState>,
    principal: Principal,
    Json(request): Json<SourcesRequest>,
) -> Result<Json<SourcesAnswer>, Failure> {
    let camera = camera(&state, principal, request.connection)?;
    let timeout = live_sources::timeout(&state.settings.state());
    let answer = match request.overlay {
        SourcesOverlay::Mqtt => camera_questions::analytics_sources(&camera, timeout)
            .await
            .map(SourcesAnswer::Mqtt),
        SourcesOverlay::DataHub => camera_questions::datahub_topics(&camera, timeout)
            .await
            .map(|topics| SourcesAnswer::DataHub(LiveTopics { topics })),
    };
    answer.map(Json).map_err(camera_failed)
}

#[cfg(test)]
mod tests {
    use super::{AppState, Principal, camera as resolve, defaults};
    use crate::{
        access::Policy,
        plugins::Plugins,
        test_support::{TestApp, json},
    };
    use api_types::live::{LiveConnection, WireSecret};
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use handlers::Handlers;
    use serde_json::{Value, json};
    use sessions::{CursorOptions, session::Command};
    use std::sync::Arc;
    use std::time::Duration;
    use tokio::{io::AsyncReadExt, net::TcpListener, time::timeout};
    use workspace::{Secret, intake};
    use workspace_store::DocCommand;

    fn post(uri: &str, body: &Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    /// A camera on this machine, with its login spelled out so no environment variable decides the outcome.
    fn camera(port: u16) -> Value {
        json!({
            "host": "127.0.0.1", "username": "root", "password": "pw", "protocol": "http", "port": port,
        })
    }

    #[tokio::test]
    async fn a_question_names_what_to_fix_and_reports_a_camera_that_does_not_answer() {
        let test = TestApp::new();
        let closed = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = closed.local_addr().unwrap().port();
        drop(closed);
        let ask = |connection: Value| {
            post(
                "/api/live/resolutions",
                &json!({ "connection": connection }),
            )
        };

        let (status, body) = test
            .send(ask(json!({ "host": "127.0.0.1", "head": 10000 })))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "head");

        // A variable the user names expands silently and goes to the camera, which is not there: the sources
        // question fails the same way.
        let mut sends_a_variable = camera(port);
        sends_a_variable["username"] = "$PATH".into();
        for request in [
            ask(sends_a_variable.clone()),
            post(
                "/api/live/sources",
                &json!({ "connection": camera(port), "overlay": "mqtt" }),
            ),
            post(
                "/api/live/sources",
                &json!({ "connection": camera(port), "overlay": "datahub" }),
            ),
        ] {
            let (status, body) = test.send(request).await;
            assert_eq!(status, StatusCode::BAD_GATEWAY);
            assert_eq!(
                json(&body),
                json!({ "code": "CameraFailed", "params": { "camera": "Unreachable" } })
            );
        }
    }

    /// What the extractor refuses is answered as every other failure is: an `ErrorWire` under the same status.
    #[tokio::test]
    async fn a_body_the_route_cannot_read_is_an_invalid_request_with_the_reason() {
        let test = TestApp::new();
        let unreadable = |body: &'static str| {
            Request::builder()
                .method("POST")
                .uri("/api/live/resolutions")
                .header("content-type", "application/json")
                .body(Body::from(body))
                .unwrap()
        };
        for (body, status) in [
            (
                r#"{"connection":{"head":"bad"}}"#,
                StatusCode::UNPROCESSABLE_ENTITY,
            ),
            ("{", StatusCode::BAD_REQUEST),
        ] {
            let (answered, bytes) = test.send(unreadable(body)).await;
            assert_eq!(answered, status);
            let error = json(&bytes);
            assert_eq!(error["code"], "InvalidRequest");
            assert!(!error["reason"].as_str().unwrap().is_empty());
        }
    }

    /// Another stream of a camera asks the camera with that stream's saved login, for the person at this machine and
    /// the same camera only. The configured password is also the person at this machine's: another principal asks
    /// with none, whatever host it names.
    #[tokio::test]
    async fn a_question_like_a_saved_stream_uses_its_login_for_the_owner_and_the_same_camera_only()
    {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let cache = folder.path().join("cache");
        let videos = crate::video_cache::start(&settings, &cache);
        let (_stop, stopping) = tokio::sync::watch::channel(false);
        let state = AppState::new(
            Arc::new(settings),
            Policy::Local,
            videos,
            stopping,
            Plugins::fixed(Handlers::built_in()),
            &cache,
        );
        state
            .settings
            .update("camera.password", |_| Some("CONFIGURED-PASSWORD".into()))
            .unwrap();
        let configured = defaults(&state.settings.state(), true);
        let saved = intake::live(
            intake::NewLive {
                host: Some("cam.local".into()),
                username: Some("root".into()),
                password: Some(Secret::new("SAVED-PASSWORD")),
                ..intake::NewLive::default()
            },
            &configured,
            &Handlers::built_in(),
        )
        .unwrap();
        let like = saved.id;
        let by_reference = intake::live(
            intake::NewLive {
                host: Some("ref.local".into()),
                username: Some("root".into()),
                password: Some(Secret::new("$PATH")),
                ..intake::NewLive::default()
            },
            &configured,
            &Handlers::built_in(),
        )
        .unwrap();
        let like_reference = by_reference.id;
        let session = state
            .sessions
            .create(true, CursorOptions::default(), Default::default())
            .unwrap();
        session
            .command(Command::Content(DocCommand::AddItems {
                items: vec![saved, by_reference],
                open: false,
            }))
            .unwrap();

        let password = |principal, host: &str, like| {
            let connection = LiveConnection {
                host: Some(host.into()),
                like,
                ..LiveConnection::default()
            };
            resolve(&state, principal, connection)
                .unwrap()
                .login
                .map(|login| login.password.expose().to_owned())
        };
        let default = password(Principal::Owner { full: true }, "cam.local", None);
        assert_eq!(default.as_deref(), Some("CONFIGURED-PASSWORD"));
        assert_eq!(
            password(Principal::Owner { full: true }, "cam.local", Some(like)).as_deref(),
            Some("SAVED-PASSWORD")
        );
        assert_eq!(
            password(Principal::Owner { full: true }, "other.local", Some(like)),
            default
        );
        // An edit sends a cleared password as empty: the saved literal it was never sent comes back, but the
        // configured one never replaces it.
        let cleared = |host: &str, like| {
            let connection = LiveConnection {
                host: Some(host.into()),
                password: Some(WireSecret::default()),
                like,
                ..LiveConnection::default()
            };
            resolve(&state, Principal::Owner { full: true }, connection)
                .unwrap()
                .login
                .map(|login| login.password.expose().to_owned())
        };
        assert_eq!(
            cleared("cam.local", Some(like)).as_deref(),
            Some("SAVED-PASSWORD")
        );
        assert_eq!(cleared("other.local", Some(like)).unwrap_or_default(), "");
        // A saved reference comes back for another stream of that camera (no password given), but not over a
        // password an edit cleared.
        let reference = |given: Option<WireSecret>| {
            let connection = LiveConnection {
                host: Some("ref.local".into()),
                password: given,
                like: Some(like_reference),
                ..LiveConnection::default()
            };
            resolve(&state, Principal::Owner { full: true }, connection)
                .unwrap()
                .login
                .map(|login| login.password.expose().to_owned())
        };
        assert_eq!(reference(None), Some(std::env::var("PATH").unwrap()));
        assert_eq!(
            reference(Some(WireSecret::default())).unwrap_or_default(),
            ""
        );
        for principal in [Principal::Owner { full: false }, Principal::None] {
            for like in [None, Some(like)] {
                // The configured user is a reference, kept literal for them, so there may be a login, never a password.
                assert_eq!(
                    password(principal, "cam.local", like).unwrap_or_default(),
                    ""
                );
            }
        }
    }

    #[tokio::test]
    async fn a_question_to_a_camera_ends_with_the_request_that_asked_it() {
        let test = TestApp::new();
        let camera_end = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = camera_end.local_addr().unwrap().port();
        let silent = tokio::spawn(async move {
            let (mut connection, _) = camera_end.accept().await.unwrap();
            let mut buffer = [0; 4096];
            // The question arrives, and the camera says nothing until the client hangs up.
            while connection.read(&mut buffer).await.unwrap() > 0 {}
        });

        let asked = test.send(post(
            "/api/live/resolutions",
            &json!({ "connection": camera(port) }),
        ));
        assert!(timeout(Duration::from_millis(300), asked).await.is_err());

        timeout(Duration::from_secs(10), silent)
            .await
            .expect("the connection closed with the request")
            .unwrap();
    }

    /// `PATH` stands for any variable: a reference with Full access reads it, one without never does.
    #[tokio::test]
    async fn a_reference_reads_the_environment_for_the_local_seat_and_stays_literal_for_a_remote_viewer()
     {
        let test = TestApp::new();
        let connection = || -> LiveConnection {
            serde_json::from_value(
                json!({ "host": "$PATH", "username": "$PATH", "password": "$PATH" }),
            )
            .unwrap()
        };
        let path = std::env::var("PATH").unwrap();
        let local = resolve(&test.state, Principal::Owner { full: true }, connection()).unwrap();
        assert_eq!(local.host, path);
        assert_eq!(local.login.unwrap().password.expose(), path);

        let remote = resolve(&test.state, Principal::Owner { full: false }, connection()).unwrap();
        assert_eq!(remote.host, "$PATH");
        let login = remote.login.unwrap();
        assert_eq!(
            (login.user.as_str(), login.password.expose()),
            ("$PATH", "$PATH")
        );
    }
}
