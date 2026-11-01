# sessions

Rules the sessions crate and its callers (the share socket, the session client) keep. The crate doc in `src/lib.rs`
says how the parts fit.

- A client is private until its Owner creates a Session, and a Session has one Owner for its whole life: ownership
  never passes on.
- Watchers never change shared state. The backend enforces it for each role; the UI only disables the controls.
- Shared state never carries settings, credentials or host paths: it is made of projection types (`ViewState` today),
  and content is named by id, never by path.
- An exploring Watcher's view is limited to what the Owner has open (`View::restrict`, re-applied on every Owner
  publish; a no-op until viewers exist).
- Resuming a seat rotates its token and replaces the older connection; anything from the older connection, such as a
  late drop or grace timer, does nothing.
- The Owner leaving ends the Session at once, and so does the Owner staying away past the 30 s grace. A Watcher
  away that long loses its seat. A Join Link dies with its Session.
- Every seat's snapshot is derived from one revision of the Session's state, so a seat's view never arrives out of
  order with it.
