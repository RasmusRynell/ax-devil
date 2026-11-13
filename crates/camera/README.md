# camera

Everything that talks to an Axis camera (services layer, no workspace-crate dependencies, runs on tokio). The only
crate that may use `reqwest`, `http-auth`, `retina`, `rumqttc` or `tokio-tungstenite`, and only the `live` crate may
depend on it (`scripts/check-architecture.cjs`). Feature `fake` adds `CameraError::fake`, for the app's tests.

- Errors are typed where they arise (`ErrorKind`, from statuses and transport errors; never from text), and
  `ErrorKind::retryable` decides whether a retry can help (RTSP also retries `NotFound`: the stream may be starting).
- A login (`Secret` password) is sent only in answer to the camera's 401 challenge: Digest, or Basic when it is the
  only scheme offered, over HTTPS (the DataHub token call and the RTSP tunnel also over plain HTTP). The one place a
  login arrives inside a URL is a custom `StreamRequest::url`, accepted as typed (the app approves a login's
  destination from the same `url`-crate parse, `workspace/src/live/endpoint.rs`); it is taken out into a `Login` at
  once, so every URL this crate builds or connects to has none, and no `Debug` shows a password. The detail
  of a failed RTSP step or a transport error holds nothing the request or the camera said: a socket error is told by
  its kind, a failed RTSP step by a closed set of phrases (`rtsp/failure.rs`) with at most a status code, because
  retina's text quotes the URL, its query and the camera's answer. Other details may name what the caller itself gave
  (a scan target that is not an address, the MQTT data source asked for). For the same reason the app's logging never writes retina's own records (`logging`'s
  `SILENT_TARGETS`); `retina_log_layer` still reads them. `Device::identify` sends no login, so it is safe on an
  unknown address.
- RTSP (`open_stream`): a task owns connect, read and reconnect. The login goes to retina apart from the URL.
  **Known limitation:** retina 0.4.21 answers whatever challenge the camera sends, so a camera (or anything in the
  path) that offers only Basic on plain `rtsp://` gets the password in cleartext. Retina has no hook to refuse a
  scheme, unlike `Device`; Axis cameras offer Digest by default.
  Retina only logs a refused keepalive, so `retina_log_layer` catches that record: a 401 or 403 ends the stream as
  `LoginRejected`, without a reconnect; any other refusal is ignored. **A process that opens streams must install the
  layer in its `tracing` subscriber** (the app's logging and the `probe` example do; the subscriber's `init` must
  also install the `log` bridge), or a refused keepalive goes unnoticed. It reaches the
  stream through a thread-local set around the one poll of the session that can send a keepalive, so streams cannot
  affect each other.
  Retina types only a response status (`Error::status_code`), so a failure without one is `Unreachable` before the
  connection is up and `Lost` after. Retina's depacketizers drop the RTP header extension that carries the capture
  time, so `rtsp/assemble.rs` and `rtsp/metadata.rs` rebuild frames and documents from raw packets: whatever lost
  packets, broke a fragment sequence or outgrew its limit is discarded whole. Video drops to the next intact keyframe
  when the reader lags or a frame is damaged; metadata never drops silently, it becomes a `Feed::Gap`. `epoch` grows
  with every reconnect.
- MQTT (`open_mqtt`): a lease (`mqtt/lease.rs`) saves the camera's MQTT client state, points the client at the broker
  and reuses or creates a publisher; `close` (or a terminal failure) removes only a publisher it created and restores
  the saved state, and the status changes after that. The lease is held across broker reconnects. A record is kept
  until its undo succeeded; if the camera cannot be restored the status is `Failed` and says what may remain.
  A data source the camera does not offer fails the lease as `NotFound` before the camera is touched, and the log says
  which sources it offers instead. Acquisition is never cut short by a close, so `close` may take a few camera timeouts. A camera has one MQTT client,
  so a second `open_mqtt` for it in the same process fails with `ErrorKind::Busy`. A dropped `LiveMessages` logs a
  warning, as it cannot clean up. A camera whose MQTT client already has a password is refused untouched
  (`ErrorKind::Unrestorable`): the camera reports it masked, so it could not be put back. A config with this crate's
  own client id, or the `ax-devil/temp/` one of the Python `ax-devil-mqtt` it replaces, is a leftover of an
  interrupted run: it is taken over and left inactive. Nothing on the camera tells a leftover from a live lease in
  another process, so `Busy` holds only within one process: two processes (say the app and `probe mqtt`) on one camera
  stop each other's analytics. Broker refusals are typed from rumqttc's `ConnectReturnCode`.
- DataHub (`open_datahub`, `Device::datahub_topics`): a session token from `wssession.cgi` through `Device`'s login
  rules (Digest first, Basic only when alone, over plain HTTP too, as in the Python app), then a WebSocket upgrade to the same host and port with
  the token in the URL. Unlike the Python app it reconnects with backoff, `epoch` growing like the others. A rejected
  login, a redirect and a protocol failure (invalid JSON, a damaged sample, an RPC error) are `Unsupported` or
  `InvalidTarget`, so they end in `Failed`. A quiet topic is not an error (a connection with no sample after 10 s logs one warning and keeps waiting); a dead connection is found by a ping every
  30 s. Not covered by tests: the `wss://` path.
- Scan (`scan`): a TCP probe of the web ports, then `Device::identify` on each open one; a login goes only to a host
  that answered as an Axis camera. A 429, or a 403 after a refusal, is asked again after 1 s, then 2 s. Dropping the receiver stops the scan. `ScanPorts` is injectable so
  tests use ephemeral ports. Not covered by tests: the blank-range default (the local /24) and the HTTPS port.
  `check` signs in to one host with one login the app holds, with the same rules (identify first).
- Redirects are not followed. HTTPS certificates are not verified (decision D12); handshake signatures are checked.
- Raw bytes only: no pixel decoding and no XML parsing here.
- `StreamRequest::url` replaces the generated stream URL and is used as typed: `rtsp://`, `http://` or `https://`. A
  login in it (percent-decoded) wins over the `Camera`'s and is taken out of the URL; any other scheme, or none with a
  host, fails the stream for good (`rtsps://` as `Unsupported`, the rest `InvalidTarget`), because the intake accepts
  any text.
- RTSP through the web server (`RtspRoute::WebApi`, or an `http(s)://` URL), for a network that blocks the RTSP port:
  `rtsp/tunnel.rs` opens the `x-rtsp-tunnelled` GET (the camera's side) and POST (base64, one run per RTSP message)
  itself, `rtsp/relay.rs` accepts retina's one connection on a loopback port and joins the two, and retina plays
  `rtsp://127.0.0.1:<port>/…` unchanged (Digest, keepalive, TEARDOWN, reconnect). Every attempt builds a new tunnel.
  The 401 of the GET is answered once, with Digest, or Basic when it is the only scheme offered (over plain HTTP too:
  the web interface has no other); the POST reuses that challenge with the next nonce count. Chunked responses and
  redirects are refused. The POST announces 32767 bytes and is never renewed, so a camera that closes at that length
  ends the session as `Lost` and the stream reconnects. Not verified on hardware: that the camera accepts the
  loopback address as the RTSP request authority. The relay's listener takes the first connection to its loopback port
  within the timeout, so a local process that is quicker than retina could take the stream.
- The seam: the app calls `Device`, `open_stream`, `open_mqtt` and `open_datahub` with plain values (`Camera`,
  `Broker`, `Login`) and reads `LiveStream` and `LiveMessages`, their `Status` and `Stats`. It does not retry or
  reconnect, match on error text, hold a login outside a `Secret`, or touch retina, rumqttc, reqwest, http-auth or
  tokio-tungstenite. It decides what to do with an
  `ErrorKind` through `retryable`, and always calls `close().await` on a `LiveMessages`.
- Tests: in-process fakes on `127.0.0.1` in `tests/it/support/` (device API with the MQTT endpoints, also used by the scan tests, RTSP, an MQTT broker
  on rumqttc's packet codec, DataHub on axum's WebSocket). rumqttd was too heavy to embed: it brings a second axum, a metrics exporter and an older
  TLS stack, and cannot drop a connection on demand.
- Real camera: `cargo run -p camera --example probe -- info|snapshot FILE|stream [SECONDS]|mqtt [SECONDS] [SOURCE]`
  reads `AX_DEVIL_TARGET_*` (and `AX_DEVIL_MQTT_BROKER_*` for `mqtt`). `cargo test -p camera --test it real_camera:: -- --ignored --test-threads=1` runs
  `tests/it/real_camera.rs`; each test skips itself unless `AX_DEVIL_TARGET_ADDR` is set, and it is never part of
  `make check` or CI.
