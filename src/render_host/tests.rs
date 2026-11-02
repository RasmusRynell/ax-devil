use super::*;
use api_types::{
    playback::MATCH_EXACT,
    render::{RENDER_PROTOCOL, pixels_message},
};
use bytes::Bytes;
use export::{LaneStyle, RenderLane};
use foundation::{
    CancelToken, ColorDesc, FrameId, FrameLayout, Matrix, MediaTime, PixelFormat, Plane,
};
use page::{Handshake, Page, own_group};
use std::{
    io::{Read, Write},
    net::{Ipv4Addr, TcpStream},
    process::{Command, Stdio},
    sync::{Arc, Mutex, mpsc},
    thread,
    time::Duration,
};
use tungstenite::WebSocket;

type Socket = WebSocket<TcpStream>;
/// What the fake page does once it has connected, given the graphics it was started with.
type Script = Arc<dyn Fn(Graphics, u16, String) + Send + Sync>;

fn connect_as_page(port: u16) -> Socket {
    let stream = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).expect("the host listens");
    tungstenite::client(format!("ws://127.0.0.1:{port}"), stream)
        .expect("the handshake completes")
        .0
}

fn say(socket: &mut Socket, message: &PageMessage) {
    socket
        .send(Message::text(serde_json::to_string(message).expect("json")))
        .expect("sent");
}

fn hello(socket: &mut Socket, protocol: u32, secret: &str) {
    say(
        socket,
        &PageMessage::Hello {
            protocol,
            secret: secret.to_owned(),
        },
    );
}

/// A process that does nothing for a minute, standing for Electron, and the script that plays the page on a thread.
fn spawner(script: Script, started: Arc<Mutex<Vec<Graphics>>>) -> Spawn {
    Box::new(move |graphics, handshake: &Handshake| {
        started.lock().unwrap().push(graphics);
        let child = Command::new("sh")
            .args(["-c", "exec sleep 60"])
            .stdout(Stdio::piped())
            .spawn()
            .expect("starts sleep");
        let (script, port, secret) = (
            Arc::clone(&script),
            handshake.port,
            handshake.secret.clone(),
        );
        thread::spawn(move || script(graphics, port, secret));
        Ok(Page::new(child, None))
    })
}

fn start(
    script: impl Fn(Graphics, u16, String) + Send + Sync + 'static,
) -> Result<RenderHost, RenderError> {
    RenderHost::with_spawner(
        spawner(Arc::new(script), Arc::default()),
        Graphics::Gpu,
        &CancelToken::new(),
    )
}

fn setup(width: u32, height: u32) -> RenderSetup {
    let lane = RenderLane {
        source: Some(0),
        catalog: "{}".to_owned(),
        style: LaneStyle {
            label: "lane".to_owned(),
            enabled_classes: Vec::new(),
            class_options: 0,
            hidden: 0,
            theme: None,
            retained_opacity: 1.0,
            filter: None,
        },
    };
    RenderSetup {
        output: Output::I420,
        picture: FrameSize { width, height },
        max_output: FrameSize {
            width: 64,
            height: 64,
        },
        lanes: vec![lane],
    }
}

fn picture() -> VideoFrame {
    let plane = |stride, len| Plane { stride, len };
    let layout = FrameLayout {
        width: 4,
        height: 2,
        format: PixelFormat::I420,
        color: ColorDesc {
            matrix: Matrix::Bt709,
            full_range: false,
        },
        planes: [plane(4, 8), plane(2, 2), plane(2, 2)],
    };
    let id = FrameId {
        sequence_id: 0,
        timestamp: MediaTime::ZERO,
    };
    VideoFrame::new(id, true, layout, Bytes::from(vec![0; 12])).expect("a frame")
}

fn item(sample: u64) -> SampleItem {
    SampleItem {
        source: 0,
        data_revision: 1,
        sample,
        frame: 0,
        serial: 0,
        item: Arc::from(vec![0_u8; 8]),
    }
}

fn draw<'a>(seq: u64, picture: &'a VideoFrame, items: &'a [SampleItem]) -> Draw<'a> {
    Draw {
        seq,
        picture,
        items,
        cells: &[0],
        kinds: &[MATCH_EXACT],
    }
}

/// A Pixels message of a 4x2 I420 picture, answering `seq`.
fn pixels(seq: u64) -> Message {
    Message::Binary(pixels_message(&export::Pixels {
        seq,
        output: Output::I420,
        width: 4,
        height: 2,
        stride0: 4,
        stride1: 2,
        data: Bytes::from_static(&[10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 30, 31]),
    }))
}

/// Answers `Setup` with a 4x2 output, then every `Render`, reporting the kind of each binary message that came
/// before it. Frame `lie_at` is answered with the wrong sequence number.
fn serve(mut socket: Socket, seen: mpsc::Sender<Vec<u8>>, lie_at: u64) {
    let Ok(Message::Text(_)) = socket.read() else {
        return;
    };
    say(
        &mut socket,
        &PageMessage::Ready {
            width: 4,
            height: 2,
            renderer: "fake".to_owned(),
        },
    );
    let mut kinds = Vec::new();
    while let Ok(message) = socket.read() {
        match message {
            Message::Binary(bytes) => kinds.push(bytes[5]),
            Message::Text(text) => {
                let Ok(HostMessage::Render { seq, .. }) = serde_json::from_str(&text) else {
                    return;
                };
                seen.send(std::mem::take(&mut kinds)).ok();
                let answered = if seq == lie_at { seq + 100 } else { seq };
                if socket.send(pixels(answered)).is_err() {
                    return;
                }
            }
            _ => {}
        }
    }
}

#[test]
fn only_a_page_with_the_secret_is_taken_and_one_built_for_another_protocol_is_refused() {
    let (stranger_done, stranger_result) = mpsc::channel();
    let host = start(move |_, port, secret| {
        // A program that found the port, and did not get the secret, knocks first.
        let mut stranger = connect_as_page(port);
        hello(&mut stranger, RENDER_PROTOCOL, "not-the-secret");
        stranger_done
            .send(matches!(stranger.read(), Err(_) | Ok(Message::Close(_))))
            .ok();
        let mut page = connect_as_page(port);
        hello(&mut page, RENDER_PROTOCOL, &secret);
        thread::sleep(Duration::from_millis(500));
    });
    assert!(
        host.is_ok(),
        "the page still connects after the stranger was turned away"
    );
    assert!(
        stranger_result
            .recv_timeout(Duration::from_secs(10))
            .unwrap(),
        "the stranger is disconnected"
    );

    let refused = start(|_, port, secret| {
        let mut page = connect_as_page(port);
        hello(&mut page, RENDER_PROTOCOL + 1, &secret);
        thread::sleep(Duration::from_millis(500));
    });
    let error = refused
        .err()
        .expect("a page on another protocol is refused");
    assert!(
        error
            .reason()
            .contains(&format!("protocol {}", RENDER_PROTOCOL + 1)),
        "{error}"
    );
}

#[test]
fn frames_go_out_items_first_and_come_back_in_order_and_only_as_the_job_describes() {
    let (seen_tx, seen) = mpsc::channel();
    let mut host = start(move |_, port, secret| {
        let mut socket = connect_as_page(port);
        hello(&mut socket, RENDER_PROTOCOL, &secret);
        serve(socket, seen_tx.clone(), 3);
    })
    .expect("starts");
    assert_eq!(
        host.setup(setup(4, 2)).expect("set up"),
        FrameSize {
            width: 4,
            height: 2
        }
    );
    assert_eq!(host.renderer(), "fake");
    assert!(host.setup(setup(4, 2)).is_err(), "a host serves one job");

    let picture = picture();
    host.submit(&draw(1, &picture, &[item(0), item(1)]))
        .unwrap();
    host.submit(&draw(2, &picture, &[])).unwrap();
    let first = host.receive().unwrap();
    assert_eq!(
        (first.seq, &first.data[..8], &first.data[10..]),
        (1, &[10, 11, 12, 13, 14, 15, 16, 17][..], &[30, 31][..])
    );
    assert_eq!(host.receive().unwrap().seq, 2);
    // Sample envelopes (kind 4) come before the frame (kind 1), and a frame with no new items sends only the frame.
    assert_eq!(seen.recv().unwrap(), [4, 4, 1]);
    assert_eq!(seen.recv().unwrap(), [1]);

    assert!(host.receive().is_err(), "nothing is waiting");
    let two_lanes = Draw {
        cells: &[0, 0],
        kinds: &[0, 0],
        ..draw(3, &picture, &[])
    };
    assert!(
        host.submit(&two_lanes).is_err(),
        "cells must match the lanes of the job"
    );
    host.submit(&draw(3, &picture, &[])).unwrap();
    let error = host
        .receive()
        .expect_err("a picture for another frame is refused");
    assert!(error.reason().contains("frame 103"), "{error}");
}

#[test]
fn a_page_that_cannot_start_on_the_gpu_is_started_again_on_software_once() {
    let started: Arc<Mutex<Vec<Graphics>>> = Arc::default();
    let script: Script = Arc::new(|graphics, port, secret| {
        let mut socket = connect_as_page(port);
        hello(&mut socket, RENDER_PROTOCOL, &secret);
        let Ok(Message::Text(_)) = socket.read() else {
            return;
        };
        match graphics {
            Graphics::Gpu => say(
                &mut socket,
                &PageMessage::Failed {
                    reason: "no WebGL2".to_owned(),
                },
            ),
            Graphics::Software => say(
                &mut socket,
                &PageMessage::Ready {
                    width: 4,
                    height: 2,
                    renderer: "SwiftShader".to_owned(),
                },
            ),
        }
        thread::sleep(Duration::from_millis(500));
    });
    let mut host = RenderHost::with_spawner(
        spawner(script, Arc::clone(&started)),
        Graphics::Gpu,
        &CancelToken::new(),
    )
    .expect("starts");

    host.setup(setup(4, 2)).expect("software rendering works");

    assert_eq!(
        *started.lock().unwrap(),
        [Graphics::Gpu, Graphics::Software]
    );
    assert_eq!(host.renderer(), "SwiftShader");
}

#[test]
fn a_page_that_cannot_render_on_software_either_fails_the_setup_with_its_reason() {
    let mut host = start(|_, port, secret| {
        let mut socket = connect_as_page(port);
        hello(&mut socket, RENDER_PROTOCOL, &secret);
        while let Ok(Message::Text(_)) = socket.read() {
            say(
                &mut socket,
                &PageMessage::Failed {
                    reason: "no WebGL2".to_owned(),
                },
            );
        }
    })
    .expect("starts");

    let error = host.setup(setup(4, 2)).expect_err("both launches fail");
    assert_eq!(error.reason(), "no WebGL2");
    assert!(host.submit(&draw(1, &picture(), &[])).is_err());
}

#[test]
fn dropping_the_host_kills_the_page_process() {
    let (stdout_tx, stdout_rx) = mpsc::channel();
    let spawn: Spawn = Box::new(move |_, handshake| {
        let mut child = Command::new("sh")
            .args(["-c", "exec sleep 60"])
            .stdout(Stdio::piped())
            .spawn()
            .expect("starts sleep");
        stdout_tx.send(child.stdout.take().expect("piped")).ok();
        let (port, secret) = (handshake.port, handshake.secret.clone());
        thread::spawn(move || {
            let mut socket = connect_as_page(port);
            hello(&mut socket, RENDER_PROTOCOL, &secret);
            thread::sleep(Duration::from_secs(5));
        });
        Ok(Page::new(child, None))
    });
    let host = RenderHost::with_spawner(spawn, Graphics::Gpu, &CancelToken::new()).expect("starts");
    let mut stdout = stdout_rx.recv().unwrap();

    drop(host);

    // The process holds the only write end of its stdout, so the read ends when it does.
    let (ended_tx, ended) = mpsc::channel();
    thread::spawn(move || {
        let _ = stdout.read(&mut [0; 1]);
        ended_tx.send(()).ok();
    });
    assert!(
        ended.recv_timeout(Duration::from_secs(10)).is_ok(),
        "the page process is still running"
    );
}

#[test]
fn a_page_that_only_pings_cannot_hold_a_cancelled_job() {
    let cancel = CancelToken::new();
    let spawn = spawner(
        Arc::new(|_, port, secret| {
            let mut socket = connect_as_page(port);
            hello(&mut socket, RENDER_PROTOCOL, &secret);
            let Ok(Message::Text(_)) = socket.read() else {
                return;
            };
            say(
                &mut socket,
                &PageMessage::Ready {
                    width: 4,
                    height: 2,
                    renderer: "fake".to_owned(),
                },
            );
            // Alive and talking, but never sends a picture.
            while socket.send(Message::Ping(Vec::new().into())).is_ok() {
                thread::sleep(Duration::from_millis(20));
            }
        }),
        Arc::default(),
    );
    let mut host = RenderHost::with_spawner(spawn, Graphics::Gpu, &cancel).expect("starts");
    host.setup(setup(4, 2)).expect("set up");
    host.submit(&draw(1, &picture(), &[])).unwrap();
    let canceller = cancel.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(300));
        canceller.cancel();
    });

    let started = std::time::Instant::now();
    let error = host.receive().expect_err("the wait ends");

    assert!(error.reason().contains("cancelled"), "{error}");
    assert!(started.elapsed() < Duration::from_secs(5));
}

#[cfg(unix)]
#[test]
fn a_hung_page_is_killed_with_everything_it_started_and_its_folder_removed() {
    // Like Electron behind its npm wrapper: a process that ignores stdin and starts another that lives on.
    let scratch = tempfile::tempdir().expect("a folder");
    let path = scratch.path().to_owned();
    let mut command = Command::new("sh");
    let mut child = own_group(&mut command)
        .args(["-c", "sleep 60 & wait"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("starts the fake electron");
    let mut stdout = child.stdout.take().expect("piped");
    let page = Page::new(child, Some(scratch));

    drop(page);

    // Both processes hold a write end of stdout, so the read ends only when both are gone.
    let (ended_tx, ended) = mpsc::channel();
    thread::spawn(move || {
        let _ = stdout.read(&mut [0; 1]);
        ended_tx.send(()).ok();
    });
    assert!(
        ended.recv_timeout(Duration::from_secs(10)).is_ok(),
        "a process the page started is still running"
    );
    assert!(!path.exists(), "the page's folder is still there");
}

#[test]
fn a_connection_that_trickles_bytes_cannot_hold_a_cancelled_start() {
    let cancel = CancelToken::new();
    let spawn = spawner(
        Arc::new(|_, port, _| {
            // A Hello frame (masked, 4095 bytes of text) sent a byte at a time, fast enough that no read times out, and
            // ended on its own so a broken host fails the test instead of hanging it.
            let mut socket = connect_as_page(port);
            let head = [0x81, 0xfe, 0x0f, 0xff, 1, 2, 3, 4];
            for byte in head.iter().chain(std::iter::repeat(&b'a')).take(400) {
                if socket.get_mut().write_all(&[*byte]).is_err() {
                    return;
                }
                thread::sleep(Duration::from_millis(20));
            }
        }),
        Arc::default(),
    );
    let canceller = cancel.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(300));
        canceller.cancel();
    });

    let started = std::time::Instant::now();
    let error = RenderHost::with_spawner(spawn, Graphics::Gpu, &cancel)
        .err()
        .expect("the start ends");

    assert!(error.reason().contains("cancelled"), "{error}");
    assert!(started.elapsed() < Duration::from_secs(3));
}
