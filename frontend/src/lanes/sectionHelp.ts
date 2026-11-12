/** Help shared by the lane controls and media tools, including empty lists. */
export const SECTION_HELP = {
  sync: "Overlay sync: Previous allows the latest earlier sample within the tolerance. Exact disables this fallback. One sample may match several frames.",
  sticky:
    "Sticky overlays: Allow earlier overlays between matches. Expiry uses the original sample timestamp; pausing does not use it up. Opacity controls reused overlays.",
  filter:
    "Filter: Hide objects by class, score or confidence in drawings, hover, the Objects list and export. File scope uses classes only. Ranges ignore missing scores and confidence.",
  catalog:
    "Catalog: How this lane draws overlays. Only the Owner can save a default for new views. Catalog files are monitored for changes.",
  details:
    "Details: Which overlay features are drawn. Hidden drawing features do not remove object data.",
  Objects:
    "Objects: Search frame objects by id, class, state or attribute; ? explains syntax. File scope dims objects absent from this frame.",
  Events:
    "Events: File events are placed on video frames. Events on the displayed frame are highlighted. Live events are logged. Times are video position for files and UTC for live streams. Search by kind, object or label; ? explains the syntax.",
} as const;
