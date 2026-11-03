//! A bare Session socket client for the tests that start the real backend: JSON in, JSON out.

// Each test crate that includes this module uses a different part of it.
#![allow(dead_code)]

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    time::Duration,
};
use tokio::{net::TcpStream, time::timeout};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async, tungstenite::Message};

/// Bounds every wait, so a broken backend fails the test instead of hanging it.
const WAIT: Duration = Duration::from_secs(20);

/// A WebSocket to the backend.
pub struct Socket(WebSocketStream<MaybeTlsStream<TcpStream>>);

impl Socket {
    /// Connects to `path` of the backend at `address`.
    pub async fn connect(address: &str, path: &str) -> Self {
        Self(
            connect_async(format!("ws://{address}{path}"))
                .await
                .unwrap()
                .0,
        )
    }

    /// Opens the Session socket and says hello as the owner of a session of its own, with the server password if
    /// there is one.
    pub async fn own(address: &str, password: Option<&str>) -> Self {
        let mut socket = Self::connect(address, "/api/session").await;
        let hello = json!({
            "type": "hello",
            "auth": {"type": "own", "serverPassword": password},
            "cursors": {"show": true, "share": true},
        });
        socket.say(hello).await;
        socket
    }

    /// Sends one message.
    pub async fn say(&mut self, message: Value) {
        self.0
            .send(Message::text(message.to_string()))
            .await
            .unwrap();
    }

    /// The next message that satisfies `wanted`, skipping the rest.
    pub async fn until(&mut self, mut wanted: impl FnMut(&Value) -> bool) -> Value {
        loop {
            let message = timeout(WAIT, self.0.next())
                .await
                .expect("no message in time")
                .expect("the socket closed")
                .expect("the socket failed");
            if let Message::Text(text) = message {
                let message: Value = serde_json::from_str(&text).unwrap();
                if wanted(&message) {
                    return message;
                }
            }
        }
    }

    /// The first binary message that satisfies `wanted`, skipping the rest.
    pub async fn until_binary(&mut self, mut wanted: impl FnMut(&[u8]) -> bool) -> Vec<u8> {
        loop {
            let message = timeout(WAIT, self.0.next())
                .await
                .expect("no message in time")
                .expect("the socket closed")
                .expect("the socket failed");
            if let Message::Binary(bytes) = message
                && wanted(&bytes)
            {
                return bytes.to_vec();
            }
        }
    }

    /// The Doc once none of its items is pending.
    pub async fn settled_doc(&mut self) -> Value {
        self.until(|message| {
            let items = &message["doc"]["workspace"]["items"];
            message["type"] == "doc"
                && items.as_array().is_some_and(|items| {
                    !items.is_empty()
                        && items
                            .iter()
                            .all(|item| item["resolution"]["status"] != "Pending")
                })
        })
        .await
    }

    /// Reads until the socket closes; the code of the close frame, 0 when the connection just dropped.
    pub async fn closed(&mut self) -> u16 {
        loop {
            match timeout(WAIT, self.0.next())
                .await
                .expect("the socket stayed open")
            {
                Some(Ok(Message::Close(frame))) => {
                    return frame.map_or(1005, |frame| frame.code.into());
                }
                Some(Ok(_)) => {}
                Some(Err(_)) | None => return 0,
            }
        }
    }
}

/// `GET path` on the backend at `address` with the server `password`, and the JSON body of the answer.
pub async fn get(address: &str, path: &str, password: &str) -> Value {
    let (address, path, password) = (address.to_owned(), path.to_owned(), password.to_owned());
    tokio::task::spawn_blocking(move || {
        let mut stream = std::net::TcpStream::connect(&address).unwrap();
        write!(
            stream,
            "GET {path} HTTP/1.1\r\nHost: {address}\r\nAuthorization: Bearer {password}\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
    })
    .await
    .unwrap()
}

/// `POST path` with the JSON `body` to the backend at `address`, and the JSON body of the answer.
pub async fn post(address: &str, path: &str, body: &Value) -> Value {
    let (address, path, body) = (address.to_owned(), path.to_owned(), body.to_string());
    tokio::task::spawn_blocking(move || {
        let mut stream = std::net::TcpStream::connect(&address).unwrap();
        write!(
            stream,
            "POST {path} HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(response.starts_with("HTTP/1.1 200"), "{response}");
        serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
    })
    .await
    .unwrap()
}
