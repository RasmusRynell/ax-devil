# sessions

Rules the sessions crate and its callers (the Session socket, the playback socket, the session client) keep. The
crate doc in `src/lib.rs` and the module docs in `src/session/` say how the parts fit.

- Every window has a Session from its first connect, with that window as its one Owner for the Session's whole life:
  ownership never passes on. A Session is private until its Owner turns sharing on, and it stays when sharing stops.
- Watchers change shared state only as far as their `Access` allows. The backend enforces it for every use; the UI
  only disables the controls.
- Shared state never carries settings, credentials or host paths. What a Watcher learns of the Doc is decided in one
  place, the projection in `src/session/project.rs`, and content is named by id, never by path.
- An exploring Watcher's view is limited to what the Doc has open (`restrict`, re-applied after every Doc change).
- Resuming a seat rotates its token and replaces the older connection; anything from the older connection, such as a
  late drop or grace timer, does nothing.
- The Owner leaving ends the Session at once, and so does the Owner staying away past the 30 s grace. A Watcher
  away that long loses its seat. A Join Link dies with its Session, with `Stop` and with `RegenerateLink`.
- Every seat's view of the Session is derived from one revision of its state, so a seat's view never arrives out of
  order with it.

## The Session service (`session`)

- Every change is one pure `transition(&SessionState, Input)` under the session's lock. A refusal changes nothing,
  except that a refused View still settles its sequence number (`views_done`). `Effects` (spawn, cancel, revoke,
  retarget) run after the lock is released; the authority a stream is told carries the transition that asked for it,
  so an older change never overwrites a newer one.
- `authorize` is the only place that decides what a seat may do, commands and streams alike. Its matches are
  exhaustive: a new `Use`, `Scope`, `StreamKey` or content kind does not compile until its policy is decided.
  `Use::Compute` is reserved and refuses every seat; who may compute what is undecided.
- Authority is checked against three generations: the `SharingGen` of a "Start sharing" (new for every `Share`,
  never reused), the `ContentGen` of the resolved Content in the stream key, and the seat's `Lease`. After every
  transition each ticket and grant is authorized again; one that fails is removed for good, a grant id is never
  reissued, and its holder is told `Revoked`. `Stop` removes every watcher in that one transition.
- A ticket is 128 random bits, single use, expires after `TICKET_TTL`, and is bound to session, seat, lease, sharing
  and stream key. A seat holds at most 8 streams (granted or ticketed), a session 64.
- A job completion goes through the same lock and is dropped when the session is gone, the job is no longer
  registered, or its owner moved on (an item resolving under a newer attempt, a seat that left or lost permission).
  Whatever makes the owner move on cancels the job in the same transition.
- A Following seat is bound to the presenter's clock for the item; a seat with its own view chooses its own frames
  at low priority, the owner at high. Live streams have no position to follow, only a priority.
- Names (`Guest N`), colors and join order are presence data kept next to the cursors, outside the revisioned state:
  a seat keeps the lowest free name and color it was given for life, resumes included, and the roster is a feed of its
  own that also reports a seat's cursor option.
