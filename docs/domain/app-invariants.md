# App Invariants

Domain invariants for Workspace content, viewers, the sidebar, UI lifecycle, plugins and settings. What belongs here
and the rest of the rules: [invariants](invariants.md).

## Content Model

- Workspace content (Seekable Video, Live Video, Overlay and Playlist Content, Entry Lanes) describes what a Workspace
  Item resolves to. It stores source specs and metadata, never opened sources or factories; runtime sources are
  created when a viewer opens content.
- Workspace Items are the truth and Content is derived: everything that adds content creates items, and Content comes
  only from resolving one. The Workspace, the Doc and the file format never branch on item kind.
- Content ids derive from the owning item's id and never change between resolutions; Content records its item's id.
  Content built outside resolution has empty ids and is never top-level Content.
- Live Stream Items store connection values as entered; a `$VARIABLE` reference is expanded only during resolution,
  silently (D18). A camera property (protocol, ports) is stored on the item and editable when adding it; changed
  defaults only prefill new items. An environment preference (connection timeout) is never stored; it is read at
  connection.
- An item's label is what the user named it, or empty; empty shows a default name derived from the recipe. Derived
  names such as an expanded host are never stored or saved.
- An item that fails to resolve stays in the Workspace with its error recorded. An item the file format cannot read
  always fails to resolve and is saved back unchanged.
- Seekable video accepts file overlays; live video accepts at most one RTSP, MQTT or DataHub WebSocket overlay, chosen
  as a typed mode.
- Every playlist lane references Seekable Video Content. Playlists are offline workflows.
- One intake validates decoder selections and live connection settings for every video and live stream opened by a
  dialog or the CLI. A resolver's playlists enter as built; it owns their validity.

## Workspace And Viewers

- Open, focused, pinned, on-screen and live-status facts about viewers come from the hosted viewers, not from parallel
  state records.
- Viewer navigation reads Hidden state through a read-only query of the Doc, never by commanding it.
- The Doc holds the current Workspace, each item's resolution, Hidden refs and open items, and changes only by a
  command applied under its Session's lock; a refused command changes nothing, and each change reaches every seat as
  one new Doc. "Modified" is current ≠ saved. One projection turns the resolved Content and the open items into rows.
- Hidden refs identify current Workspace content, playlist entries or lanes; orphan and out-of-range refs are ignored.
- A lane's matching (basis, tolerance, sticky) lives in its item's recipe, by the lane's slot (the same in every entry
  of an item's playlists, where lane names can differ), and changes only by a Doc command.
- Removing Content removes its whole item and closes every viewer showing it. Opening a Workspace file closes every
  viewer, even when item ids are kept. The split host alone removes and destroys hosted viewers.
- Every item is listed: a failed one is an unavailable row showing its reason that never opens; one still resolving is
  a pending row that opens nothing.
- Items resolve off the UI thread and the lock; a result commits only while its item still waits on that attempt.
- Only adding items opens a viewer. Opening or creating a Workspace lists its items and connects nothing.
- Nothing prompts on launch, open, New or quit: a Workspace with unsaved edits has one recovery copy, never its own
  file, that exists exactly while the edits do.

## Sidebar

- The window is always in a Workspace; the activity bar has a Workspace zone and an app zone.
- A Workspace-zone view is the whole Workspace or one part of it: it lists the Workspace's own items and offers ways
  to add to them, and keeps no data of its own beyond what the Workspace file saves.
- An app-zone view is about the application and never shows Workspace content.
- A sidebar view adds nothing to the viewer area; opening content from it uses the Workspace's one open-content path.

## UI Lifecycle

- UI is a projection of runtime state, not its owner. Components receive state from the module or service that owns
  the concept, and building a component never rebuilds shared runtime state as a side effect. The exception is view
  state, what the window shows of its content: the client owns it and publishes it.
- Work that discovers files, reads disk, validates, compiles, builds indexes, opens sources or creates shared caches
  belongs behind the owning runtime boundary, never in UI construction.
- Pixels inside rendered frames (catalogs, sheets, export burn-ins) follow the frame, not the design tokens or theme
  (UI tokens, zoom and the near-black video surface: [write-ui skill](../../.agents/skills/write-ui/SKILL.md)).
- Components that own runtime resources have an explicit cleanup contract, and their owner calls it.
- The UI never blocks on file, network, decode, compile or index work, and never waits by nesting its event loop.
  Finite work runs as cancellable tasks owned by a scope that closes when its owner cleans up; a cancelled task
  delivers nothing. Work that changes something outside the app, or streams, keeps its own runner and finishes
  regardless of listeners.
- There is one cancellation contract; cancellation propagates through every layer a task calls, and generic error
  handling cannot swallow it.
- Opened media has one owner at a time and is handed over explicitly; resources are destroyed on the executor they
  require, and an unclaimed result's resources are released.
- Callbacks from replaced or cleaned-up live sources cannot affect the viewer. Sources own their workers; quit waits
  for retiring sources within one deadline, so transport cleanup such as removing temporary device config
  completes.
- Live sources report failures they retry themselves (reconnecting) and failures needing a manual retry (error), each
  with a reason the viewer shows, never only the logs.
- Memory management never pauses frame delivery.

## Plugin System

- Plugins load only from the registry that install wrote, never because their files exist. Plugin ids are unique
  among plugins and handler ids among all decoders and resolvers.
- Load failures never stop the application; each is recorded and listed.
- A plugin runs in an empty working folder with an allowlisted environment; its process group is killed whenever the
  run ends ([trust](../plugins.md#trust)).
- Install, update and remove change only the registry, atomically, and reach the running app: work in flight
  keeps its handlers, new opens use the current ones. A plugin's dependencies are its own.
- Resolver settings are a declared schema the host renders as a form and as CLI arguments, never plugin UI code.

## Settings And Shortcuts

- Domain code takes settings as values; only the settings service reads and writes them.
- Settings apply on OK. Settings that need a restart carry one shared marker, and saving one offers to restart after
  the exit-time saves. Quick Setup is the one exception.
- A save names the keys it changes and validates the resulting state; the file is replaced atomically and only then
  are Owners notified; a failed save changes nothing. Startup storage folders stay active until restart.
- Settings live in one immutable state replaced whole; consumers subscribe to changes, never poll.
- Each settings row is Personal (how my app behaves) or Server (what the server works with); an Owner reads and
  writes both, and a page that holds only a join link keeps Personal settings in its browser. A masked password sent back
  in a records row means unchanged.
- On/off overlay preferences are one closed set; adding a member adds it to the View menu, Settings and the config.
- All shortcuts are defined in one table; persistence is override-only, so no overrides means all defaults apply. An
  override wins over any default with the same key, which becomes unbound.
- Settings are defaults, not references: nothing created from them refers back to them or embeds a value or credential.
- A settings file this build cannot read stops startup with an error naming the file and key; format and migration:
  [`crates/settings/README.md`](../../crates/settings/README.md).
