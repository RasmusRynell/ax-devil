//! A snapshot in flight cannot replace a shared live feed's picture or connecting state.
use super::*;
use crate::{
    LiveSources, Update,
    fake::{FakeConnector, content, matching, unit},
};
use handlers::Handlers;
use std::{sync::Arc, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    sync::Semaphore,
};
use workspace::{LiveOverlay, Protocol};

#[tokio::test]
async fn a_snapshot_finishing_after_a_shared_feed_opens_is_superseded() {
    tokio::time::timeout(Duration::from_secs(10), async {
        for with_picture in [false, true] {
            let started = Arc::new(Semaphore::new(0));
            let release = Arc::new(Semaphore::new(0));
            let (seen, gate) = (started.clone(), release.clone());
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let server = tokio::spawn(async move {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    request.push(socket.read_u8().await.unwrap());
                }
                assert!(request.starts_with(b"GET /axis-cgi/jpg/image.cgi"));
                seen.add_permits(1);
                gate.acquire().await.unwrap().forget();
                let jpeg = include_bytes!("../../../../fixtures/video/rtsp-snapshot.jpg");
                let headers = format!("HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", jpeg.len());
                socket.write_all(headers.as_bytes()).await.unwrap();
                socket.write_all(jpeg).await.unwrap();
            });
            let fake = FakeConnector::default();
            let live = Arc::new(LiveSources::new(Arc::new(fake.clone())));
            let glances = Arc::new(Glances::default());
            let mut content = content("127.0.0.1", LiveOverlay::None);
            content.camera.protocol = Protocol::Http;
            content.camera.port = port;
            // A previously started, now closed stream is allowed to request a camera snapshot.
            drop(live.open(&content, &Handlers::built_in(), matching()).await.unwrap());
            let request = {
                let (live, glances, content) = (live.clone(), glances.clone(), content.clone());
                tokio::spawn(async move { glances.stream_picture(&live, &content, false).await })
            };
            started.acquire().await.unwrap().forget();
            let mut viewer = live
                .open(&content, &Handlers::built_in(), matching())
                .await
                .unwrap();
            let expected = if with_picture {
                let video = fake.videos().last().unwrap().clone();
                video
                    .status
                    .send(camera::Status::Live { epoch: 1 })
                    .unwrap();
                video.units.send(unit(1, Some(1000), 20)).await.unwrap();
                loop {
                    if let Some(Update::Frame(frame)) = viewer.next().await {
                        break glances.picture(&frame.frame).await;
                    }
                }
            } else {
                None
            };
            release.add_permits(1);
            assert_eq!(request.await.unwrap(), expected);
            drop(viewer);
            live.shutdown(Duration::from_secs(1)).await;
            server.await.unwrap();
        }
    })
    .await
    .expect("snapshot and shared feed complete");
}
