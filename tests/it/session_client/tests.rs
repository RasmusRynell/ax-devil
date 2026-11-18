use super::*;
use futures_util::stream;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[tokio::test(start_paused = true)]
async fn unrelated_heartbeats_cannot_extend_the_search_deadline() {
    let seen = Arc::new(AtomicUsize::new(0));
    let observed = seen.clone();
    let started = Instant::now();
    let search = tokio::spawn(async move {
        let frames = stream::unfold(0, |n| async move {
            tokio::time::sleep(Duration::from_secs(5)).await;
            let frame = if n % 2 == 0 {
                Message::Ping(Vec::new().into())
            } else {
                Message::text(json!({"type": "heartbeat"}).to_string())
            };
            Some((Ok(frame), n + 1))
        });
        until_json(
            &mut Box::pin(frames),
            "resolved workspace document",
            |message| {
                assert_eq!(message["type"], "heartbeat");
                observed.fetch_add(1, Ordering::Relaxed);
                false
            },
        )
        .await
    });
    // A resetting timeout would still be searching when this independent bound expires.
    let failure = timeout(Duration::from_secs(35), search)
        .await
        .expect("heartbeat traffic extended the search")
        .unwrap_err();
    assert_eq!(started.elapsed(), Duration::from_secs(20));
    assert!(seen.load(Ordering::Relaxed) > 0, "heartbeat was skipped");
    let panic = failure.into_panic();
    assert_eq!(
        panic.downcast_ref::<String>().unwrap(),
        "Timed out waiting for resolved workspace document"
    );
}
