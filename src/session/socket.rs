//! The Session socket: one WebSocket per seat, from `Hello` to the close. The sessions crate decides what each seat
//! may do; this adapter decides who may be seated, keeps the connection honest (limits, liveness, deadlines) and
//! speaks the wire protocol (`reader` and `writer`; the close codes are in `exit`).

use super::{
    FIRST_MESSAGE_TIMEOUT,
    exit::{ENDED, Exit, FULL, INVALID_CREDENTIAL, INVALID_DATA, NOT_ALLOWED, finish},
    reader, writer,
};
use crate::{
    AppState,
    access::{Principal, Slot},
};
use api_types::{SessionAuth, SessionClientMessage};
use axum::{
    extract::{ConnectInfo, State, WebSocketUpgrade, ws::Message, ws::WebSocket},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use futures_util::{StreamExt, stream::SplitStream};
use sessions::{
    JoinLink, Secret, SessionId,
    session::{Handle, Overlays, Refusal},
};
use std::net::SocketAddr;
use tokio::{sync::mpsc, time::timeout};

/// Frames and messages bigger than this are not this protocol.
const MAX_FRAME_BYTES: usize = 16 * 1024;
const MAX_MESSAGE_BYTES: usize = 64 * 1024;
/// Answers the reader may have waiting for the writer.
const REPLIES: usize = 32;

/// The Session socket upgrade.
pub(crate) async fn socket(
    State(state): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    upgrade: WebSocketUpgrade,
) -> Response {
    let slot = match state.gate.admit(peer.ip()) {
        Ok(slot) => slot,
        Err(refused) => {
            return (StatusCode::TOO_MANY_REQUESTS, refused.to_string()).into_response();
        }
    };
    upgrade
        .max_frame_size(MAX_FRAME_BYTES)
        .max_message_size(MAX_MESSAGE_BYTES)
        .on_upgrade(move |socket| async move {
            // An upgraded connection would hold the backend's shutdown open, so the socket ends with the backend.
            let mut stopping = state.stopping.clone();
            tokio::select! {
                () = run(&state, slot, socket) => {}
                _ = stopping.wait_for(|stopping| *stopping) => {}
            }
        })
}

/// Runs one socket until it ends. Dropping the seat at the end is the disconnect that starts its grace period.
async fn run(state: &AppState, mut slot: Slot, socket: WebSocket) {
    let (mut sink, mut stream) = socket.split();
    let (handle, owner) = match hello(state, &mut slot, &mut stream).await {
        Ok(seated) => seated,
        Err(exit) => return finish(&mut sink, exit).await,
    };
    let (replies, outgoing) = mpsc::channel(REPLIES);
    let result = tokio::select! {
        result = reader::run(state, &mut stream, &handle, replies) => result,
        result = writer::run(state, &mut sink, &handle, outgoing, owner) => result,
    };
    let Err(exit) = result;
    if matches!(exit, Exit::Left) {
        handle.leave();
    }
    finish(&mut sink, exit).await;
}

/// The overlays a new Session's Presenter View starts with: the Owner's settings, each on while the row has no boolean.
fn starting_overlays(settings: &settings::State) -> Overlays {
    let on = |key| {
        settings
            .resolved(key)
            .and_then(|value| value.as_bool())
            .unwrap_or(true)
    };
    Overlays {
        object_details: on("overlays.object_details"),
        lane_names: on("overlays.lane_names"),
    }
}

/// Whether the hello's credential makes the connection an Owner, who is told when settings change. A resumed socket
/// is judged by the credential its hello carries again; the resume itself never depends on it.
fn presents_owner(state: &AppState, auth: &SessionAuth) -> bool {
    match auth {
        SessionAuth::Own { server_password }
        | SessionAuth::Attach {
            server_password, ..
        } => state
            .policy
            .principal_for(server_password.as_deref())
            .is_owner(),
        SessionAuth::Join { .. } => false,
    }
}

/// Reads `Hello` and seats the socket; the flag is whether it is an Owner's.
async fn hello(
    state: &AppState,
    slot: &mut Slot,
    stream: &mut SplitStream<WebSocket>,
) -> Result<(Handle, bool), Exit> {
    let first = timeout(FIRST_MESSAGE_TIMEOUT, async {
        loop {
            match stream.next().await {
                Some(Ok(Message::Ping(_) | Message::Pong(_))) => {}
                other => return other,
            }
        }
    })
    .await
    .map_err(|_| Exit::close(NOT_ALLOWED, "no hello in time"))?;
    slot.hello_received();
    let text = match first {
        Some(Ok(Message::Text(text))) => text,
        Some(Ok(Message::Binary(_))) => return Err(Exit::close(INVALID_DATA, "expected text")),
        _ => return Err(Exit::Gone),
    };
    let Ok(SessionClientMessage::Hello {
        auth,
        resume,
        cursors,
    }) = serde_json::from_str(&text)
    else {
        return Err(Exit::close(INVALID_DATA, "expected hello"));
    };
    let sessions = &state.sessions;
    let owner = presents_owner(state, &auth);
    // The token alone is the credential of a resume, so `auth` is not looked at.
    if let Some(token) = resume {
        let token = token
            .parse::<Secret>()
            .map_err(|error| refuse(error, ENDED))?;
        let handle = sessions
            .resume(&token)
            .map_err(|error| refuse(error, ENDED))?;
        // The client sent its current choices; they replace the ones the seat had when it dropped, before the first
        // part is built.
        handle.set_cursor_options(cursors);
        return Ok((handle, owner));
    }
    match auth {
        SessionAuth::Own { server_password } => {
            let principal = member(state, server_password.as_deref())?;
            // Only Full access lets what the Owner types read this machine's environment.
            let handle = sessions
                .create(
                    principal.full_access(),
                    cursors,
                    starting_overlays(&state.settings.state()),
                )
                .map_err(|error| refuse(error, ENDED))?;
            if let Some(rx) = sessions.subscribe(handle.session()) {
                state.files.attach(handle.session(), rx);
                // Only the first session of the Local listener starts from what the command line asked for.
                if state.policy.is_local()
                    && let Some(launch) = state.launch.take()
                {
                    state.files.launch(&handle, launch).await;
                }
            }
            Ok((handle, owner))
        }
        SessionAuth::Attach {
            session,
            server_password,
        } => {
            let principal = member(state, server_password.as_deref())?;
            let id = session
                .parse::<SessionId>()
                .map_err(|error| refuse(error, ENDED))?;
            sessions
                .attach(id, principal.full_access(), cursors)
                .map(|handle| (handle, owner))
                .map_err(|error| refuse(error, ENDED))
        }
        SessionAuth::Join { link } => {
            let link = link
                .parse::<JoinLink>()
                .map_err(|error| refuse(error, INVALID_CREDENTIAL))?;
            sessions
                .join(&link, cursors)
                .map(|handle| (handle, owner))
                .map_err(|error| refuse(error, INVALID_CREDENTIAL))
        }
    }
}

/// Who the password makes the caller, if that is an Owner, who may own or attach to sessions.
fn member(state: &AppState, password: Option<&str>) -> Result<Principal, Exit> {
    let principal = state.policy.principal_for(password);
    if password.is_some() && principal == Principal::None {
        return Err(Exit::close(INVALID_CREDENTIAL, "invalid password"));
    }
    if principal.is_owner() {
        Ok(principal)
    } else {
        Err(Exit::close(NOT_ALLOWED, "this needs the server password"))
    }
}

/// `malformed` is the code for text that is not an id, link or token: what the caller was trying to present.
fn refuse(refusal: Refusal, malformed: u16) -> Exit {
    let code = match refusal {
        Refusal::Invalid => malformed,
        Refusal::Link => INVALID_CREDENTIAL,
        Refusal::Full => FULL,
        // Whatever else keeps a caller out, the session is not there for it.
        Refusal::SeatGone
        | Refusal::NotAllowed
        | Refusal::NotSharing
        | Refusal::AlreadySharing
        | Refusal::ContentUnavailable
        | Refusal::TooManyStreams
        | Refusal::BadTicket => ENDED,
    };
    Exit::Close(code, refusal.to_string())
}
