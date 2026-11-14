# sessions

Rules the sessions crate and its callers (the Session socket, the playback socket, the session client) keep. The
crate doc in `src/lib.rs` and the module docs in `src/session/` say how the parts fit.

- Every window has a Session from its first connect. Any number of Owner seats can attach to it, and all drive its one
  Presenter View. A Session is private until an Owner turns sharing on, and it stays when sharing stops.
- Guests change shared state only as far as their `Access` allows. The backend enforces it for every use; the UI
  only disables the controls.
- Shared state never carries settings, credentials or host paths. What a Guest learns of the Doc is decided in one
  place, the projection in `src/session/project.rs`, and content is named by id, never by path.
- An exploring Guest's view is limited to what the Doc has open (`restrict`, re-applied after every Doc change): the
  viewers of a closed item go, and their panes give their space to the sibling. A view of more than `MAX_PANES` panes
  is refused.
- Resuming a seat rotates its token and replaces the older connection; anything from the older connection, such as a
  late drop or grace timer, does nothing.
- The last Owner seat leaving, or away past the 30 s grace, ends the Session at once; any other Owner seat is only
  removed. A Guest never keeps it alive and loses its seat when away that long. A Join Link dies with its Session,
  with `Stop` and with `RegenerateLink`.
- Every seat's view of the Session is derived from one revision of its state, so a seat's view never arrives out of
  order with it.

## The Session service (`session`)

- Every change is one pure `transition(&SessionState, Input)` under the session's lock. A refusal changes nothing,
  except that a View always settles its sequence number (`views_done`), refused or not. The `Effects` that revoke or
  retarget a stream and cancel a job take effect under the same lock, before the new state is published, so nobody
  sees the new state while a stream or job still follows the old one; only the start of a job runs after the lock.
- `authorize` is the only place that decides what a seat may do, commands and streams alike. Its matches are
  exhaustive: a new `Use`, `Scope` or content kind does not compile until its policy is decided. The `Can` flags a
  seat is sent come from the same table, so no client keeps a copy of it.
- Authority is checked against three generations: the `SharingGen` of a "Start sharing" (new for every `Share`,
  never reused), the `ContentGen` in the stream's `ContentRef`, and the seat's `Lease`. After every transition each
  ticket and grant is authorized again; one that fails is removed for good, a grant id is never reissued, and its
  holder is told `Revoked`. `Stop` removes every Guest in that one transition.
- A ticket is 128 random bits, single use, expires after `TICKET_TTL`, and is bound to session, seat, lease, sharing
  and `ContentRef`. A seat holds at most 8 streams (granted or ticketed), a session 64.
- A job completion goes through the same lock and is dropped when the session is gone, the job is no longer
  registered, or its owner moved on (an item resolving under a newer attempt, a seat that left or lost permission).
  Whatever makes the owner move on cancels the job in the same transition.
- A Following seat is bound to the clock of the presenter's viewer its `ContentRef` names, and may stream only the
  entry that viewer shows of the stream's item, so the presenter switching that viewer's entry revokes it and no other
  viewer's; a seat with its own view chooses its own entry and frames at low
  priority, an Owner seat at high. `restrict` alone decides which entry a viewer shows: the one asked for while the
  Content has it, else the first not hidden, else the first. The presenter's clock intents are stamped in server time: a changed intent is
  clamped to the last two seconds, an unchanged one keeps the stamp it was stored with.
- Names (`Guest N`), colors and join order are presence data kept next to the cursors, outside the revisioned state:
  a seat keeps the lowest free name and color it was given for life, resumes included, and the roster is a feed of its
  own that also reports a seat's cursor option.
