//! The backend's integration tests, one binary: the routes, the sockets and the commands.
//! Tests that set process environment variables (`live_transport`, the export tests) are their own binaries.

mod access;
mod bookmarks;
mod common;
mod glance;
mod local_video;
mod mot_playlist;
mod playback_catalogs;
mod playback_export;
mod playback_inspect;
mod playback_lanes;
mod playback_ws;
mod plugins;
mod prepare_next;
mod session_client;
mod session_socket;
mod shutdown;
mod workspace_files;
mod workspace_recovery;
mod workspace_session;
