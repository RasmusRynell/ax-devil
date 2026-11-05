//! Talk to a real camera without the app: `cargo run -p camera --example probe -- info`, `... -- snapshot FILE`,
//! `... -- stream [SECONDS]` (video and metadata, default 10 s) or `... -- mqtt [SECONDS] [DATA_SOURCE]` (analytics
//! over MQTT, default 10 s and the camera's first data source).
//!
//! Reads `AX_DEVIL_TARGET_ADDR` (host), `AX_DEVIL_TARGET_USER` and `AX_DEVIL_TARGET_PASS`; the device API is HTTPS on
//! port 443 unless `AX_DEVIL_TARGET_PROTOCOL=http`. `mqtt` also reads `AX_DEVIL_MQTT_BROKER_ADDR`,
//! `AX_DEVIL_MQTT_BROKER_USER`, `AX_DEVIL_MQTT_BROKER_PASS` and, when the port is not 1883, `AX_DEVIL_MQTT_BROKER_PORT`.
// The one place in this crate that prints: a command-line tool's output is its result.
#![allow(clippy::print_stdout)]

use std::env;
use std::process::ExitCode;
use std::time::Duration;

use camera::{
    AccessUnit, Broker, Camera, CameraError, Device, ErrorKind, Feed, Head, Host, Login, Reconnect,
    Scheme, Secret, SourceKey, Status, StreamRequest, Web, open_mqtt, open_stream,
};
use tokio::time::{interval, sleep};

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match run(&args).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            // Stdout is the only output this tool has; the failure goes there too.
            println!("error: {message}");
            ExitCode::FAILURE
        }
    }
}

async fn run(args: &[String]) -> Result<(), String> {
    let camera = camera_from_env().map_err(|e| e.to_string())?;
    let device = Device::new(&camera).map_err(|e| e.to_string())?;
    match args.first().map(String::as_str) {
        Some("info") => info(&device).await.map_err(|e| e.to_string()),
        Some("stream") => {
            let seconds = match args.get(1) {
                Some(text) => text.parse().map_err(|_| "usage: probe stream [SECONDS]")?,
                None => 10,
            };
            stream(&camera, Duration::from_secs(seconds)).await
        }
        Some("mqtt") => {
            let seconds = match args.get(1) {
                Some(text) => text
                    .parse()
                    .map_err(|_| "usage: probe mqtt [SECONDS] [DATA_SOURCE]")?,
                None => 10,
            };
            let key = match args.get(2) {
                Some(key) => SourceKey(key.clone()),
                None => device
                    .analytics_sources()
                    .await
                    .map_err(|e| e.to_string())?
                    .into_iter()
                    .next()
                    .ok_or("the camera lists no analytics data source")?,
            };
            mqtt(&camera, &device, key, Duration::from_secs(seconds)).await
        }
        Some("snapshot") => {
            let file = args.get(1).ok_or("usage: probe snapshot FILE")?;
            let jpeg = device
                .snapshot(Head::FIRST)
                .await
                .map_err(|e| e.to_string())?;
            std::fs::write(file, &jpeg).map_err(|e| format!("cannot write {file}: {e}"))?;
            println!("wrote {} bytes to {file}", jpeg.len());
            Ok(())
        }
        _ => Err(
            "usage: probe info | snapshot FILE | stream [SECONDS] | mqtt [SECONDS] [DATA_SOURCE]"
                .to_owned(),
        ),
    }
}

/// Who the camera is without a login, then (when a login is set) with it, and what it offers.
async fn info(device: &Device) -> Result<(), CameraError> {
    match device.identify().await {
        Ok(identity) => println!("identified: {identity:?}"),
        Err(error) if error.kind == ErrorKind::LoginRequired => println!("identify needs a login"),
        Err(error) => return Err(error),
    }
    println!("signed in: {:?}", device.sign_in().await?);
    let image = device.image_sizes().await?;
    let first = image.heads.iter().find(|(head, _)| *head == Head::FIRST);
    let sizes: Vec<String> = first
        .map_or(&image.common, |(_, own)| own)
        .iter()
        .map(ToString::to_string)
        .collect();
    println!("resolutions: {}", sizes.join(", "));
    let sources = device.analytics_sources().await?;
    println!(
        "analytics sources: {}",
        sources
            .iter()
            .map(|s| s.0.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    Ok(())
}

/// What arrived in one second.
#[derive(Default)]
struct Second {
    frames: u32,
    keyframes: u32,
    documents: u32,
    last_capture: Option<i64>,
    /// The smallest and largest step between the capture times of consecutive frames, in nanoseconds.
    steps: Option<(i64, i64)>,
}

impl Second {
    fn video(&mut self, unit: &AccessUnit) {
        self.frames += 1;
        self.keyframes += u32::from(unit.keyframe);
        let Some(capture) = unit.capture else { return };
        if let Some(last) = self.last_capture {
            let step = capture.0 - last;
            let (low, high) = self.steps.unwrap_or((step, step));
            self.steps = Some((low.min(step), high.max(step)));
        }
        self.last_capture = Some(capture.0);
    }

    fn print(&self) {
        let steps = self.steps.map_or_else(
            || "no capture times".to_owned(),
            |(low, high)| {
                format!(
                    "capture steps {:.1} to {:.1} ms",
                    low as f64 / 1e6,
                    high as f64 / 1e6
                )
            },
        );
        println!(
            "video {} fps, {} keyframes, {steps}; metadata {} documents",
            self.frames, self.keyframes, self.documents
        );
    }
}

/// Streams video and metadata for `duration`, printing status changes and a line per second.
async fn stream(camera: &Camera, duration: Duration) -> Result<(), String> {
    let mut stream = open_stream(
        camera,
        StreamRequest {
            head: Head::FIRST,
            video: Some(None),
            metadata: true,
            url: None,
            reconnect: Reconnect::RTSP,
        },
    );
    let mut second = Second::default();
    let mut ticks = interval(Duration::from_secs(1));
    let end = sleep(duration);
    // Ctrl-C ends the run the way the time running out does, so the camera is closed and left clean.
    let interrupt = tokio::signal::ctrl_c();
    tokio::pin!(end, interrupt);
    let outcome = loop {
        tokio::select! {
            Some(unit) = stream.video.recv() => second.video(&unit),
            Some(feed) = stream.metadata.recv() => match feed {
                Feed::Item(doc) => {
                    if second.documents == 0 {
                        println!("metadata: {} bytes, capture {:?}", doc.xml.len(), doc.capture);
                    }
                    second.documents += 1;
                }
                Feed::Gap { lost } => println!("metadata gap: {lost} lost"),
            },
            Ok(()) = stream.status.changed() => {
                let status = stream.status.borrow_and_update().clone();
                println!("status: {status:?}");
                if let Status::Failed(error) = status {
                    break Err(error.to_string());
                }
            }
            _ = ticks.tick() => {
                second.print();
                second = Second { last_capture: second.last_capture, ..Second::default() };
            }
            () = &mut end => break Ok(()),
            _ = &mut interrupt => break Ok(()),
        }
    };
    println!("stats: {:?}", stream.stats());
    stream.close().await;
    outcome
}

/// Subscribes to `key` for `duration`, printing status changes and a line per message, then shows the camera's
/// publishers before and after so a leftover is easy to see.
async fn mqtt(
    camera: &Camera,
    device: &Device,
    key: SourceKey,
    duration: Duration,
) -> Result<(), String> {
    let broker = broker_from_env().map_err(|e| e.to_string())?;
    let list = |label: &'static str| async move {
        let publishers = device
            .analytics_publishers()
            .await
            .map_err(|e| e.to_string())?;
        println!("{label}: {} publishers", publishers.len());
        Ok::<(), String>(())
    };
    list("before").await?;
    println!("subscribing to {}", key.0);
    let mut live = open_mqtt(camera, &broker, key, Reconnect::MQTT);
    let last_status = live.status.clone();
    let end = sleep(duration);
    // Ctrl-C ends the run the way the time running out does, so the camera is closed and left clean.
    let interrupt = tokio::signal::ctrl_c();
    tokio::pin!(end, interrupt);
    let outcome = loop {
        tokio::select! {
            Some(feed) = live.messages.recv() => match feed {
                Feed::Item(message) => println!(
                    "message: epoch {}, topic {}, {} bytes", message.epoch, message.topic, message.payload.len()
                ),
                Feed::Gap { lost } => println!("gap: {lost} lost"),
            },
            Ok(()) = live.status.changed() => {
                let status = live.status.borrow_and_update().clone();
                println!("status: {status:?}");
                if let Status::Failed(error) = status {
                    break Err(error.to_string());
                }
            }
            () = &mut end => break Ok(()),
            _ = &mut interrupt => break Ok(()),
        }
    };
    println!("stats: {:?}", live.stats());
    live.close().await;
    // Closing restores the camera; a failure to do that shows only in the final status.
    let end = last_status.borrow().clone();
    list("after").await?;
    match end {
        Status::Failed(error) => Err(error.to_string()),
        _ => outcome,
    }
}

fn broker_from_env() -> Result<Broker, CameraError> {
    let var = |name: &str| env::var(name).ok().filter(|value| !value.is_empty());
    let host = var("AX_DEVIL_MQTT_BROKER_ADDR").unwrap_or_default();
    let mut broker = Broker::new(Host::parse(&host)?);
    if let Some(port) = var("AX_DEVIL_MQTT_BROKER_PORT").and_then(|port| port.parse().ok()) {
        broker.port = port;
    }
    broker.login = var("AX_DEVIL_MQTT_BROKER_USER").map(|user| Login {
        user,
        password: Secret::new(var("AX_DEVIL_MQTT_BROKER_PASS").unwrap_or_default()),
    });
    Ok(broker)
}

fn camera_from_env() -> Result<Camera, CameraError> {
    let var = |name: &str| env::var(name).ok().filter(|value| !value.is_empty());
    let host = var("AX_DEVIL_TARGET_ADDR").unwrap_or_default();
    let mut camera = Camera::new(Host::parse(&host)?);
    if var("AX_DEVIL_TARGET_PROTOCOL").is_some_and(|p| p.eq_ignore_ascii_case("http")) {
        camera.web = Web {
            scheme: Scheme::Http,
            port: Scheme::Http.default_port(),
        };
    }
    camera.login = var("AX_DEVIL_TARGET_USER").map(|user| Login {
        user,
        password: Secret::new(var("AX_DEVIL_TARGET_PASS").unwrap_or_default()),
    });
    Ok(camera)
}
