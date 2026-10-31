# 04 Domain Rules

- **MUST** port `CONTEXT.md` as the new glossary, keeping terms and the "Avoid" column; reword rows that name current
  classes (Prepared Drawing's "Qt Quick item", Viewer Widget's class name).
- **MUST** port `docs/domain/invariants.md` as the new invariants file. Rules carry over in meaning; rewrite every
  mechanism they name (classes, signals, threads, Qt, Python, pools) using the tables below, and audit the ported
  text for any left. Scene types: `scene/model.py`, `scene/model_values.py`.

## Translate

| Section | Current wording | Neutral rule |
|---------|-----------------|--------------|
| Content Model | `workspace/core` imports no PySide6 | Layers 1–3 import no UI toolkit ([02](02-architecture.md#layers-and-dependency-direction)) |
| Coordinates | Never mutate Qt value objects or text layouts | Prepared drawings and styles are immutable values |
| Display Rendering | Newest frame queued for the GUI thread | Newest frame queued for the UI |
| Display Rendering | Scene and catalog interpretation on the GUI side; scene graph callbacks consume prepared data; GPU resources only in Qt's scene graph phase | Interpretation happens before the renderer boundary; renderer callbacks consume prepared immutable data; GPU resources only in the renderer's resource phase |
| Display Rendering | Display and export use the same Quick renderer | Display and export share rendering semantics: same instructions, order, colors and geometry |
| Scene Model | `source_data` and `debug` are picklable | Serializable by the Scene serialization schema ([05](05-formats-and-extensions.md#scene-serialization)) |
| Workspace And Viewers, Settings | Signal names (`state_changed`, `workspace_replaced`) | Change notifications with the same meaning |
| UI Lifecycle | `follow_appearance`, `chrome.theme.VIDEO_CANVAS`, `cleanup()` | Re-apply text-derived sizes and fixed colors on change; a near-black canvas token; an explicit cleanup contract |
| UI Lifecycle, Workspace And Viewers | GUI thread, worker threads, `BackgroundJobs`/`JobScope`, `ItemResolver`, `open_entry_media` | The UI never waits; owned cancellable tasks; resources destroyed on the executor they require ([03](03-runtime.md#concurrency-guarantees-must)) |
| UI Lifecycle | `CancelToken` raising `Cancelled`, no other mechanism | One runtime cancellation contract; cancellation propagates through it and generic error handling cannot swallow it |
| UI Lifecycle | `sourceReconnecting`, `sourceError` | Reconnecting and error notifications with a reason |
| Plugin System | Entry-point groups | The new discovery mechanism (D1) |
| Data Pipeline | Windows ctime caveat | Use content digests where the platform lacks a change time |

## Drop

- UI Lifecycle: the GC freeze and collection rules (Python-specific; the guarantee is in [03](03-runtime.md)).
- Plugin System: uv project, interpreter selection, `AX_DEVIL_LAUNCH_COMMAND`/`AX_DEVIL_PLUGIN_ROOT`; keep the
  guarantees (plugins never change app dependencies, management runs from the base app, failed refresh starts the
  base app).
- Data Pipeline: "one process pool" for the cache allowance; keep "one allowance shared by open offline sources".
- Class and method names quoted as requirements (`add_observation()`, `SplitView`, `WorkspaceIntake`).

## Deliberate changes

| Rule | Change | Reason |
|------|--------|--------|
| Live queues | Bounded per owner; codec-aware video discard; lossless payload decode, overflow is a lane error; drop-oldest only after decode; every drop counted ([03](03-runtime.md#real-time-delivery-must)) | Designs out the burst stall. *Current:* the oldest undecoded metadata is dropped with only a log warning (`data_sources/live/rtsp_source.py`, `_on_metadata`) |
| Settings access | Domain code takes values; no global settings | Testability ([02](02-architecture.md#seams-to-redraw)) |
| Resolver settings | Declared schema, not a plugin widget (D4) | Plugins become UI-toolkit independent |

## Neutral types to fix early

- Media Time: signed 64-bit integer microseconds. Offline frames take it from container presentation timestamps
  (*current:* PTS frame index in `data_sources/file_data_provider/pyav_decoder/frame_index.py`); variable frame rate
  works; never synthesize timestamps from FPS. Live frames take device capture time (LV-04, [14](14-build-order.md#stack-criteria)).
- Frame identity: `(sequence_id, timestamp_us)`.
- Scene: one serialization schema from M2 on ([05](05-formats-and-extensions.md#scene-serialization)).

## Rules implied by the code but not stated as invariants

Confirm with the owner before treating as MUST.

- Bookmark kinds are a closed set `note, issue, example, question`, saved by value (`workspace/core/bookmarks.py`).
- Id colors use CRC-32, stable across runs (`scene/rendering/README.md#rules`); port the hash exactly so ID colors match.
- Hover picks the smallest box containing the cursor; routing uses the latest observation's primary classification;
  entities without observations are skipped; relation recipes need both endpoints in the filtered Scene
  (`scene/rendering/README.md#how-it-runs`).

## Porting procedure

1. Rewrite each rule using the tables above.
2. Delete rules the new type system or validation enforces by itself.
3. Link each remaining rule to the test that guards it; a rule without one is a test gap.
