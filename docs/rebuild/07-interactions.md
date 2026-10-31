# 07 User Interactions

Every flow is a parity row in [01](01-product-scope.md); its Source column defines the behavior. This file keeps only
behavior that neither the row nor its source states. Port behavior, not wording or layout.

| Flow | Rows | Behavior to keep | Source |
|------|------|------------------|--------|
| Open a workspace | WS-02 | Closes all viewers and opens none; only adding items opens a viewer | `docs/domain/invariants.md#workspace-and-viewers` |
| Remove item | WS-07 | A row older than the workspace (menu open during removal) does nothing | `docs/architecture/ui-framework.md#workspace-controller` |
| Edit item | WS-07 | Unreadable items cannot be edited | `docs/architecture/workspace.md#workspace-items` |
| Files change on disk | WS-06 | Changes on network shares are not noticed (known limit) | `file_watching/README.md` |
| Add Video | AC-01 | Several matching decoders → the user picks | `docs/architecture/overview.md#workspace-items-and-intake` |
| Drop files | AC-02 | Pane drags are never treated as file drops | `docs/architecture/ui-framework.md#workspace-shell` |
| Add Live Stream, Add Playlist | AC-03, AC-04 | The dialog stays open with the reason on failure; form work is cancelled when the form closes | `docs/architecture/workspace.md#resolution` |
| Live connection trouble | LV-02 | Retry reopens every transport from the same content | `docs/architecture/overview.md#live-source-composition` |
| Network scan | LV-05 | Settings changes never interrupt a running scan | `docs/settings.md` |
| Compare lanes | OF-02 | Policy in OF-02 | `video_viewer/README.md#rules` |
| Sync and sticky settings | OF-04, IN-07 | Old analysis stays shown until the new one finishes | `docs/architecture/overview.md#synchronization` |
| Live overlay falls behind | LV-01, LV-07 | Error state until drained, decoder reset, gap marker in Events ([03](03-runtime.md#real-time-delivery-must)) | [03](03-runtime.md#real-time-delivery-must) |

CLI flows: [08](08-settings-cli.md#cli-surface).
