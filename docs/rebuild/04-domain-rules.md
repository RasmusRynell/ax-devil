# 04 Domain Rules

`CONTEXT.md` and `docs/domain/invariants.md` were ported from the reference with the tables below, which stay as the
record of what changed from the reference wording. Scene types at the reference: `scene/model.py`,
`scene/model_values.py`.

## Translate

| Section | Current wording | Neutral rule |
|---------|-----------------|--------------|
| Content Model | `workspace/core` imports no PySide6 | Layers 1–3 import no UI toolkit ([02](02-architecture.md#layers-and-dependency-direction)) |
| Coordinates | Never mutate Qt value objects or text layouts | Sample Items are immutable values |
| Display Rendering | Newest frame queued for the GUI thread | Newest frame queued for the UI |
| Display Rendering | Scene and catalog interpretation on the GUI side; scene graph callbacks consume prepared data; GPU resources only in Qt's scene graph phase | Interpretation happens per lane in the frontend before painting, and painting reads Marks only; GPU resources only in the renderer's resource phase |
| Display Rendering | Display and export use the same Quick renderer | Display and export run the same presentation code |
| Scene Model | `source_data` and `debug` are picklable | Serializable by the Scene serialization schema ([05](05-formats-and-extensions.md#scene-serialization)) |
| Workspace And Viewers, Settings | Signal names (`state_changed`, `workspace_replaced`) | Change notifications with the same meaning |
| UI Lifecycle | `follow_appearance`, `chrome.theme.VIDEO_CANVAS`, `cleanup()` | Re-apply text-derived sizes and fixed colors on change; a near-black canvas token; an explicit cleanup contract |
| UI Lifecycle, Workspace And Viewers | GUI thread, worker threads, `BackgroundJobs`/`JobScope`, `ItemResolver`, `open_entry_media` | The UI never waits; owned cancellable tasks; resources destroyed on the executor they require ([03](03-runtime.md#concurrency-guarantees-must)) |
| UI Lifecycle | `CancelToken` raising `Cancelled`, no other mechanism | One runtime cancellation contract; cancellation propagates through it and generic error handling cannot swallow it |
| UI Lifecycle | `sourceReconnecting`, `sourceError` | Reconnecting and error notifications with a reason |
| Plugin System | Entry-point groups | The registry of manifest paths (D29) |
| Data Pipeline | Windows ctime caveat | Use content digests where the platform lacks a change time |

## Drop

- UI Lifecycle: the GC freeze and collection rules (Python-specific; the guarantee is in [03](03-runtime.md)).
- Plugin System: uv project, interpreter selection, `AX_DEVIL_LAUNCH_COMMAND`/`AX_DEVIL_PLUGIN_ROOT`; dropped. The
  guarantees stay (a plugin's dependencies are its own, through its manifest's `program`; install and remove change
  only the registry).
- Data Pipeline: "one process pool" for the cache allowance; keep "one allowance shared by open offline sources".
- Class and method names quoted as requirements (`add_observation()`, `SplitView`, `WorkspaceIntake`).

## Deliberate changes

| Rule | Change | Reason |
|------|--------|--------|
| Live queues | Bounded per owner; codec-aware video discard; lossless DataHub/embedded payload decode, overflow is a lane error (MQTT keeps only the newest waiting message); drop-oldest only after decode; every drop counted ([03](03-runtime.md#real-time-delivery-must)) | Designs out the burst stall. *Current:* the oldest undecoded metadata is dropped with only a log warning (`data_sources/live/rtsp_source.py`, `_on_metadata`) |
| Settings access | Domain code takes values; no global settings | Testability ([02](02-architecture.md#seams-to-redraw)) |
| Resolver settings | Declared schema, not a plugin widget (D4) | Plugins become UI-toolkit independent |

## Rules implied by the code but not stated as invariants

Confirm with the owner before treating as MUST.

- Bookmark kinds are a closed set `note, issue, example, question`, saved by value (`workspace/core/bookmarks.py`).
- Id colors use CRC-32, stable across runs (`scene/rendering/README.md#rules`); port the hash exactly so ID colors match.
- Hover picks the smallest box containing the cursor; routing uses the latest observation's primary classification;
  entities without observations are skipped; relation recipes need both endpoints in the filtered Scene
  (`scene/rendering/README.md#how-it-runs`).
