//! The Session socket: one WebSocket per seat, from `Hello` to the close. The sessions crate decides what each seat
//! may do; this adapter decides who may be seated, keeps the connection honest (limits, liveness, deadlines) and
//! speaks the wire protocol (`reader` and `writer`; the close codes are in `exit`).

use super::{
    FIRST_MESSAGE_TIMEOUT,
    exit::{ENDED, Exit, FULL, INVALID_CREDENTIAL, INVALID_DATA, NOT_ALLOWED, finish},
    reader, wire, writer,
};
use crate::{
    AppState,
    access::{Principal, Slot},
};
use api_types::{CursorPoint, Grant, SessionAuth, SessionClientMessage};
use axum::{
    extract::{ConnectInfo, State, WebSocketUpgrade, ws::Message, ws::WebSocket},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use futures_util::{StreamExt, stream::SplitStream};
use sessions::{
    JoinLink, Secret, SessionError, SessionId,
    session::{Command, Handle},
};
use std::net::SocketAddr;
use tokio::{sync::mpsc, time::timeout};
use workspace_store::DocCommand;

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
    let slot = match state.admission.admit(peer.ip()) {
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
    let handle = match hello(state, &mut slot, &mut stream).await {
        Ok(handle) => handle,
        Err(exit) => return finish(&mut sink, exit).await,
    };
    let (replies, outgoing) = mpsc::channel(REPLIES);
    let result = tokio::select! {
        result = reader::run(state, &mut stream, &handle, replies) => result,
        result = writer::run(state, &mut sink, &handle, outgoing) => result,
    };
    let Err(exit) = result;
    if matches!(exit, Exit::Left) {
        handle.leave();
    }
    finish(&mut sink, exit).await;
}

/// Reads `Hello` and seats the socket.
async fn hello(
    state: &AppState,
    slot: &mut Slot,
    stream: &mut SplitStream<WebSocket>,
) -> Result<Handle<CursorPoint>, Exit> {
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
    let cursors = wire::cursor_options(cursors);
    let sessions = &state.sessions;
    // The token alone is the credential of a resume, so `auth` is not looked at.
    if let Some(token) = resume {
        let token = token
            .parse::<Secret>()
            .map_err(|error| refuse(&error, ENDED))?;
        let handle = sessions
            .resume(&token)
            .map_err(|error| refuse(&error, ENDED))?;
        // The client sent its current choices; they replace the ones the seat had when it dropped, before the first
        // part is built.
        handle.set_cursor_options(cursors);
        return Ok(handle);
    }
    match auth {
        SessionAuth::Own { server_password } => {
            let principal = member(state, server_password.as_deref())?;
            let files = principal.grants().contains(&Grant::Files);
            let handle = sessions
                .create(files, cursors)
                .map_err(|error| refuse(&error, ENDED))?;
            if principal == Principal::Local {
                let items = state.launch.take();
                if !items.is_empty() {
                    // The owner of a Local session holds the files grant, so this cannot be refused.
                    let _ = handle
                        .command(Command::Content(DocCommand::AddItems { items, open: true }));
                }
            }
            Ok(handle)
        }
        SessionAuth::Watch {
            session,
            server_password,
        } => {
            member(state, server_password.as_deref())?;
            let id = session
                .parse::<SessionId>()
                .map_err(|error| refuse(&error, ENDED))?;
            sessions
                .watch(id, cursors)
                .map_err(|error| refuse(&error, ENDED))
        }
        SessionAuth::Join { link } => {
            let link = link
                .parse::<JoinLink>()
                .map_err(|error| refuse(&error, INVALID_CREDENTIAL))?;
            sessions
                .join(&link, cursors)
                .map_err(|error| refuse(&error, INVALID_CREDENTIAL))
        }
    }
}

/// Who the password makes the caller, if that may own or watch sessions: the person at this machine or a password
/// holder.
fn member(state: &AppState, password: Option<&str>) -> Result<Principal, Exit> {
    let principal = state.policy.principal_for(password);
    if password.is_some() && principal == Principal::None {
        return Err(Exit::close(INVALID_CREDENTIAL, "invalid password"));
    }
    if principal.grants().contains(&Grant::Sessions) {
        Ok(principal)
    } else {
        Err(Exit::close(NOT_ALLOWED, "this needs the server password"))
    }
}

/// `malformed` is the code for text that is not an id, link or token: what the caller was trying to present.
fn refuse(error: &SessionError, malformed: u16) -> Exit {
    let code = match error {
        SessionError::Malformed => malformed,
        SessionError::NotFound => ENDED,
        SessionError::Rejected => INVALID_CREDENTIAL,
        SessionError::Full => FULL,
    };
    Exit::Close(code, error.to_string())
}
