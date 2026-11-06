//! The routes behind the live form: the questions it asks a camera (stream sizes, analytics sources, DataHub topics).
//! The network scan's routes are in [`super::network`].
//!
//! A question connects to the camera with the form's connection, so its `$NAME` references expand as a stream item's
//! do. The question is the handler's future, so a client that drops the request ends the connection. A question about another stream of a camera (`like`) authenticates with
//! that stream's saved login on the backend, for the person at this machine only and under the rule of the intake's
//! `copy_login`. Nothing here logs or returns a login.

use crate::{AppState, access::Principal, camera_questions, live_sources, session::live::defaults};
use api_types::{
    Audience, Grant,
    live::{
        LiveConnection, LiveProblem, LiveReason, LiveResolutions, LiveTopics, NewLiveOverlayWire,
        NewLiveWire, ResolutionsRequest, SourcesAnswer, SourcesOverlay, SourcesRequest,
    },
    project,
};
use axum::{Json, extract::State, http::StatusCode};
use foundation::{CancelToken, Interrupt, UserError};
use workspace::{Content, Environment, ResolvedCamera, Source, intake};

type Problem = (StatusCode, Json<LiveProblem>);

fn problem(status: StatusCode, problem: LiveProblem) -> Problem {
    (status, Json(problem))
}

fn camera_failed(reason: LiveReason) -> Problem {
    problem(StatusCode::BAD_GATEWAY, LiveProblem::Camera { reason })
}

/// The camera `connection` names, with the settings' defaults for what it leaves out and its references expanded.
/// For the person at this machine, a blank password is the configured one, and a blank login of a connection `like` a
/// saved stream is that stream's, when the camera is the same one.
fn camera(
    state: &AppState,
    principal: Principal,
    connection: LiveConnection,
) -> Result<ResolvedCamera, Problem> {
    let settings = state.settings.state();
    let handlers = state.plugins.handlers();
    // The configured passwords are the person at this machine's: another principal names its own.
    let owner = principal.grants().contains(&Grant::Preferences);
    let mut defaults = defaults(&settings);
    if !owner {
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
        problem(
            StatusCode::UNPROCESSABLE_ENTITY,
            LiveProblem::Invalid {
                error: project(error, Audience::HOST),
            },
        )
    };
    if owner
        && let Some(held) = like.and_then(|item| state.sessions.find_item(item))
        && let Source::Live(saved) = &held.item.recipe.source
    {
        new.copy_login(saved, &defaults, intake::LoginUse::Discovery)
            .map_err(|error| invalid(&error))?;
    }
    let item = intake::live(new, &defaults, &handlers).map_err(|error| invalid(&error))?;
    match item.recipe.resolve(
        &item.id,
        &item.display_name(),
        &handlers,
        &Environment::of_process(),
        &CancelToken::new(),
    ) {
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
) -> Result<Json<LiveResolutions>, Problem> {
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
) -> Result<Json<SourcesAnswer>, Problem> {
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
    use api_types::live::LiveConnection;
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
            .send(ask(json!({ "host": "127.0.0.1", "head": 300 })))
            .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["problem"], "Invalid");
        assert_eq!(json(&body)["error"]["location"], "head");

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
                json!({ "problem": "Camera", "reason": "Unreachable" })
            );
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
        let mut values = state.settings.state().values(&settings::Scope::ALL);
        values.insert("camera.password".into(), "CONFIGURED-PASSWORD".into());
        state
            .settings
            .replace(
                &settings::Scope::ALL,
                &settings::Change {
                    values,
                    shortcuts: Default::default(),
                },
            )
            .unwrap();
        let configured = defaults(&state.settings.state());
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
        let session = state
            .sessions
            .create(true, CursorOptions::default())
            .unwrap();
        session
            .command(Command::Content(DocCommand::AddItems {
                items: vec![saved],
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
        let default = password(Principal::Local, "cam.local", None);
        assert_eq!(default.as_deref(), Some("CONFIGURED-PASSWORD"));
        assert_eq!(
            password(Principal::Local, "cam.local", Some(like)).as_deref(),
            Some("SAVED-PASSWORD")
        );
        assert_eq!(
            password(Principal::Local, "other.local", Some(like)),
            default
        );
        for principal in [Principal::Server, Principal::None] {
            for like in [None, Some(like)] {
                assert_eq!(password(principal, "cam.local", like), None);
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
}
