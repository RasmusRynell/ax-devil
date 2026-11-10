//! Finite source opens: cancellation never waits for I/O, abandoned resources are freed, and errors reach the caller.

use super::*;
use crate::test_support::hold;
use futures_util::stream;
use playback::Authority;
use std::{future::Future, time::Duration};
use tokio::sync::oneshot;

async fn bounded<T>(future: impl Future<Output = T>) -> T {
    tokio::time::timeout(Duration::from_secs(5), future)
        .await
        .expect("background work did not reach the expected state")
}

/// A returned file resource, with an acknowledgement after its actual cleanup.
struct Resource(Option<tempfile::NamedTempFile>, Option<oneshot::Sender<()>>);

impl Drop for Resource {
    fn drop(&mut self) {
        drop(self.0.take());
        let _ = self.1.take().unwrap().send(());
    }
}

#[tokio::test]
async fn cancellation_discards_a_late_resource_and_shutdown_does_too() {
    // Revocation represents a closed/replaced owner; the stop signal is backend shutdown with work in flight.
    for shutdown in [false, true] {
        let (holder, authority) = watch::channel(Authority::default());
        let mut gate = Gate::new(authority, || 0);
        let (stop, mut closing) = watch::channel(false);
        let (hold, began, release) = hold();
        let (dropped, freed) = oneshot::channel();
        let (observed, cancelled) = oneshot::channel();
        let file = tempfile::NamedTempFile::new().unwrap();
        let path = file.path().to_path_buf();
        let resource = Resource(Some(file), Some(dropped));
        let building = move |cancel: &CancelToken, _: &mut dyn FnMut(f32)| {
            hold();
            observed.send(cancel.is_cancelled()).unwrap();
            // Blocking I/O may succeed after cancellation; the owner must never receive this resource.
            Ok(resource)
        };
        let (outbox, _queue) = mpsc::channel(1);
        let mut viewer = stream::pending();
        let location = HostPath::new("/clips/a.mp4");
        let (ended, ()) = tokio::join!(
            bounded(open_video(
                building,
                &location,
                &mut viewer,
                &outbox,
                &mut closing,
                &mut gate,
            )),
            async {
                bounded(began).await.unwrap();
                assert!(path.exists());
                if shutdown {
                    stop.send_replace(true);
                } else {
                    holder.send_replace(Authority::Revoked);
                }
            }
        );
        assert!(if shutdown {
            matches!(ended, Err(End::Closed))
        } else {
            matches!(ended, Err(End::Revoked))
        });
        assert!(path.exists(), "the job is still holding its resource");
        release.send(()).unwrap();
        assert!(bounded(cancelled).await.unwrap());
        bounded(freed).await.unwrap();
        assert!(!path.exists(), "the discarded result leaked its file");
    }
}

#[tokio::test]
async fn an_open_error_reaches_the_caller_and_the_next_open_succeeds() {
    let (_holder, authority) = watch::channel(Authority::default());
    let mut gate = Gate::new(authority, || 0);
    let (_stop, mut closing) = watch::channel(false);
    let (hold, began, release) = hold();
    let error = UserError::new(ErrorCode::FileUnreadable, "The recording cannot be read")
        .at(HostPath::new("/clips/a.mp4"));
    let expected = error.clone();
    let (outbox, _queue) = mpsc::channel(1);
    let mut viewer = stream::pending();
    let location = HostPath::new("/clips/a.mp4");
    let (result, ()) = tokio::join!(
        bounded(open_video(
            move |_, _| {
                hold();
                Err::<(), _>(Interrupt::Failed(error))
            },
            &location,
            &mut viewer,
            &outbox,
            &mut closing,
            &mut gate,
        )),
        async {
            bounded(began).await.unwrap();
            release.send(()).unwrap();
        }
    );
    let Err(End::Failed(actual)) = result else {
        panic!("the caller did not receive the open failure");
    };
    assert_eq!(actual, expected);
    assert!(matches!(
        bounded(open_video(
            |_, _| Ok(7),
            &location,
            &mut viewer,
            &outbox,
            &mut closing,
            &mut gate,
        ))
        .await,
        Ok(7)
    ));
}
