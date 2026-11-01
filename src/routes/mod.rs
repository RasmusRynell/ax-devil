//! The backend's HTTP and event routes, one module per owning concept; each is a thin adapter over a service.

use foundation::CancelToken;

pub mod catalogs;
pub mod handlers;
pub mod playback;
pub mod window;
pub mod workspace;

/// Cancels the blocking work of a request or session when its future is dropped, as the server does when the
/// client goes away.
pub(crate) struct CancelOnDrop(pub(crate) CancelToken);

impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}
