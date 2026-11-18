//! Joins an open tunnel to retina through one loopback connection: what the camera sends goes to retina as it is, and
//! each RTSP message or interleaved frame retina sends goes to the camera as its own base64 run. Retina does not know
//! it is not talking to the camera's RTSP port.
//!
//! The relay ends itself when either side closes, and closes all three sockets. It is detached from the stream task
//! on purpose: dropping a retina session starts its TEARDOWN in a retina task on the same connection, and the relay
//! must stay up to carry it to the camera.

use std::io;
use std::net::Ipv4Addr;
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use bytes::{Bytes, BytesMut};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::time::timeout;

use super::tunnel::Tunnel;
use crate::error::{CameraError, ErrorKind};

/// The largest body an RTSP message may announce; retina's requests have none.
const MAX_BODY: usize = 1 << 20;
/// The longest head an RTSP message may have.
const MAX_HEAD: usize = 16 * 1024;

/// Listens on a free loopback port for the one connection retina makes, and returns the port. The listener takes that
/// connection within `limit` and no other, and closes; the tunnel is dropped if none comes.
pub(super) async fn serve(tunnel: Tunnel, limit: Duration) -> Result<u16, CameraError> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .await
        .map_err(|error| CameraError::io(ErrorKind::Unreachable, &error))?;
    let port = listener
        .local_addr()
        .map_err(|error| CameraError::io(ErrorKind::Unreachable, &error))?
        .port();
    tokio::spawn(async move {
        let Ok(Ok((socket, _))) = timeout(limit, listener.accept()).await else {
            return;
        };
        drop(listener);
        let (from_retina, to_retina) = socket.into_split();
        let Tunnel {
            get,
            leftover,
            post,
        } = tunnel;
        // The first side to end drops the other, which closes every socket.
        tokio::select! {
            _ = camera_to_retina(get, leftover, to_retina) => {}
            _ = retina_to_camera(from_retina, post) => {}
        }
    });
    Ok(port)
}

async fn camera_to_retina(
    mut get: impl AsyncRead + Unpin,
    leftover: Bytes,
    mut retina: impl AsyncWrite + Unpin,
) -> io::Result<()> {
    retina.write_all(&leftover).await?;
    tokio::io::copy(&mut get, &mut retina).await?;
    Ok(())
}

async fn retina_to_camera(
    mut retina: impl AsyncRead + Unpin,
    mut post: impl AsyncWrite + Unpin,
) -> io::Result<()> {
    let mut framer = Framer::default();
    loop {
        while let Some(message) = framer.next_message()? {
            post.write_all(STANDARD.encode(message).as_bytes()).await?;
            post.flush().await?;
        }
        if retina.read_buf(&mut framer.buffer).await? == 0 {
            // Retina is done (after its TEARDOWN, which is written): end the POST in order.
            return post.shutdown().await;
        }
    }
}

/// Cuts what retina sends into whole messages: an RTSP request (head and body), or an interleaved `$` frame.
#[derive(Default)]
struct Framer {
    buffer: BytesMut,
}

impl Framer {
    /// The next whole message, or `None` until more bytes arrive.
    fn next_message(&mut self) -> io::Result<Option<Bytes>> {
        let buffer = &self.buffer;
        let Some(&first) = buffer.first() else {
            return Ok(None);
        };
        let length = if first == b'$' {
            match buffer.get(2..4) {
                Some(length) => 4 + usize::from(u16::from_be_bytes([length[0], length[1]])),
                None => return Ok(None),
            }
        } else {
            let Some(end) = buffer.windows(4).position(|window| window == b"\r\n\r\n") else {
                return if buffer.len() > MAX_HEAD {
                    Err(invalid("an RTSP request head that never ends"))
                } else {
                    Ok(None)
                };
            };
            let body = content_length(&buffer[..end]);
            if body > MAX_BODY {
                return Err(invalid("an RTSP request body that is too large"));
            }
            end + 4 + body
        };
        if buffer.len() < length {
            return Ok(None);
        }
        Ok(Some(self.buffer.split_to(length).freeze()))
    }
}

/// The `Content-Length` of a request head; 0 without one.
fn content_length(head: &[u8]) -> usize {
    String::from_utf8_lossy(head)
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.trim()
                .eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse().ok())?
        })
        .unwrap_or(0)
}

fn invalid(what: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, what.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feed(framer: &mut Framer, bytes: &[u8]) -> Option<Bytes> {
        framer.buffer.extend_from_slice(bytes);
        framer.next_message().unwrap()
    }

    #[test]
    fn a_message_split_across_reads_is_sent_whole_and_once() {
        let message =
            b"SET_PARAMETER rtsp://h/s RTSP/1.0\r\nCSeq: 4\r\nContent-Length: 5\r\n\r\nhello";
        let mut framer = Framer::default();
        for part in message[..message.len() - 1].chunks(7) {
            assert_eq!(feed(&mut framer, part), None);
        }
        assert_eq!(
            feed(&mut framer, &message[message.len() - 1..]).as_deref(),
            Some(&message[..])
        );
        assert_eq!(framer.next_message().unwrap(), None);
    }

    #[test]
    fn an_interleaved_frame_is_one_message_and_a_request_after_it_another() {
        let mut framer = Framer::default();
        let frame = [b'$', 1, 0, 3, 9, 9, 9];
        let request = b"TEARDOWN rtsp://h/s RTSP/1.0\r\nCSeq: 5\r\n\r\n";
        framer.buffer.extend_from_slice(&frame);
        framer.buffer.extend_from_slice(request);
        assert_eq!(framer.next_message().unwrap().as_deref(), Some(&frame[..]));
        assert_eq!(
            framer.next_message().unwrap().as_deref(),
            Some(&request[..])
        );
        framer.buffer.extend_from_slice(&frame[..3]);
        assert_eq!(
            framer.next_message().unwrap(),
            None,
            "a frame head cut short"
        );
    }
}
