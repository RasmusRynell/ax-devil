/** Help shared by the lane controls and media tools, including empty lists. */
export const SECTION_HELP = {
  sync: "Overlay sync: After exact and source-provided matching, Previous allows the latest earlier sample within the tolerance. Exact disables this fallback. One sample may match several frames.",
  sticky:
    "Sticky overlays: Keep the latest earlier overlay when no new sample matches, including after seeks. Expiry uses the original sample timestamp; pausing does not use it up. Opacity controls reused overlays.",
  filter:
    "Filter: Hide objects by class, score or confidence in drawings, hover, the Objects list and export. File scope uses classes only. Objects without a score or confidence are kept.",
  catalog:
    "Catalog: Choose how this lane draws overlays. Apply to all lanes copies the choice within this viewer; Use as default starts new views with it (Owner only). Saved catalog file changes appear automatically; Reload reads it again.",
  details:
    "Details: Changes apply immediately, including while paused. Hidden drawing details remain available in inspection and hover when Show Object Details is on. Reset enables every detail.",
  Objects:
    "Objects: Search the displayed frame by id, class, state or attribute; ? explains the syntax. For files, File lists every object and dims those absent from this frame. Click an object to inspect it.",
  Events:
    "Events: For files, each event belongs to the first frame at or after its sample; its page can jump there. Events on the displayed frame are highlighted. Live events accumulate when first shown. Times are video position for files and UTC for live streams. Search by kind, object or label; ? explains the syntax.",
} as const;
