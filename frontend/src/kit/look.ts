/* The shared look of every control. Components compose these strings instead of carrying their own classes.
   Every Tailwind class is written out in full so the class scanner sees it. */

/** Box of a bordered control (button, pill); an edge from `EDGE` supplies the border colour and text. */
export const CONTROL =
  "inline-flex min-h-row cursor-pointer items-center gap-m rounded-control border bg-transparent px-m focus-ring disabled:border-line-strong/50 disabled:text-disabled";

/** Border, text and hover tint of a `CONTROL`; the Enter action looks like any other action. */
export const EDGE = "border-line-strong text-fg enabled:hover:bg-hover";

/** `CONTROL` for a label that wraps a hidden checkbox or radio, so state comes from `:has()` instead of the element. */
export const CONTROL_IN =
  "inline-flex min-h-row items-center gap-s rounded-control border border-line-strong bg-transparent px-m text-fg focus-ring-within has-enabled:not-has-checked:hover:bg-hover has-disabled:border-line-strong/50 has-disabled:text-disabled";

/** Chrome without a border: menu-bar triggers, list rows, tabs, headers, dismiss and on-canvas icon buttons. */
export const QUIET =
  "inline-flex min-h-row items-center gap-m rounded-control bg-transparent px-m text-fg enabled:hover:bg-hover focus-ring disabled:text-disabled";

/** Selected look for one-of-many (radio) controls inside `CONTROL_IN`: solid accent; disabled keeps the muted edge. */
export const CHOSEN =
  "has-checked:border-accent has-checked:bg-accent has-checked:font-medium has-checked:text-surface has-disabled:has-checked:border-line-strong/50";

/** Selected look for any-number (checkbox) controls inside `CONTROL_IN`: accent tint; disabled keeps the muted edge. */
export const CHECKED =
  "has-checked:border-accent/50 has-checked:bg-accent/15 has-checked:hover:bg-accent/25 has-disabled:has-checked:border-line-strong/50";

/** The status colours a chip or banner can carry; the names are the colour tokens. */
export type Tone = "neutral" | "success" | "caution" | "danger";

/** Classes per tone: `text` for text and icon, `fill` and `press` for a tinted pill (press only when clickable), `edge` for an outline. */
export const TONE: Record<Tone, { text: string; fill: string; press: string; edge: string }> = {
  neutral: {
    text: "text-muted",
    fill: "bg-hover",
    press: "hover:bg-line",
    edge: "border-line-strong",
  },
  success: {
    text: "text-success",
    fill: "bg-success/15",
    press: "hover:bg-success/25",
    edge: "border-success/50",
  },
  caution: {
    text: "text-caution",
    fill: "bg-caution/15",
    press: "hover:bg-caution/25",
    edge: "border-caution/50",
  },
  danger: {
    text: "text-danger",
    fill: "bg-danger/15",
    press: "hover:bg-danger/25",
    edge: "border-danger/50",
  },
};

/** The kinds of thing the app shows apart by colour; the names are the colour tokens. */
export type Kind = "live" | "video" | "playlist" | "lane";

/** The text colour (so an icon's) of each kind. */
export const KIND: Record<Kind, string> = {
  live: "text-kind-live",
  video: "text-kind-video",
  playlist: "text-kind-playlist",
  lane: "text-kind-lane",
};

/** A text input, drop-down or other single-line field; focus shows as an accent border, not the ring buttons use. */
export const FIELD =
  "h-row rounded-control border border-line-strong bg-popup px-s text-fg placeholder:text-muted focus:border-accent focus:outline-none disabled:border-line-strong/50 disabled:text-disabled";

/** `FIELD` for a label around a search input: the accent border follows focus inside it. */
export const FIELD_IN =
  "h-row rounded-control border border-line-strong bg-popup px-s text-fg placeholder:text-muted focus-within:border-accent has-disabled:border-line-strong/50 has-disabled:text-disabled";

/** Pill sizing: small text, never wider than its container, no wrapping. */
export const PILL = "max-w-full min-w-0 whitespace-nowrap text-small";

/** Icon-only control: one row square with the icon centred; a caller's own size class wins (see `square-row`). */
export const SQUARE = "square-row justify-center px-0";
