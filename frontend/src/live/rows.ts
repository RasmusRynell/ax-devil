/** The rows of the Live view: a camera per camera the workspace streams, with a row per stream under it, and a row per
 * camera the scan found that the workspace does not hold. */

import type { LiveConfirmation, NetworkRow } from "../generated/api";
import type { IconName } from "../kit/icons";
import { running, type Camera, type Stream } from "./cameras";
import type { Checks } from "./checks";
import { compareScans, SIGN_IN, uptimeText, type Order } from "./network";
import {
  CONNECTING,
  IDLE,
  MARKS,
  mostUrgent,
  type MarkColor,
  type StreamStatus,
} from "./streamState";

/** What a row can do: its inline button (the first of its menu) and the entries of its menu. */
export type RowAction = "start" | "stop" | "add" | "addStream" | "information" | "remove";

/**
 * The menu text, icon and hover text of each action; the button without text is named by its hover text. `group` is
 * where the menu draws a line: between opening, inspecting and editing.
 */
export const ACTIONS: Record<
  RowAction,
  { menu: string; icon?: IconName; tip: string; group: number }
> = {
  start: { menu: "Start stream", icon: "play", tip: "", group: 0 },
  stop: { menu: "Stop stream", icon: "square-filled", tip: "", group: 0 },
  add: { menu: "Add", icon: "plus", tip: "", group: 0 },
  addStream: {
    menu: "Add stream",
    icon: "plus",
    tip: "Add another stream from this camera",
    group: 0,
  },
  information: { menu: "Information", tip: "", group: 1 },
  remove: { menu: "Remove", tip: "", group: 2 },
};

/** What the Filter can hide a row by: a value in a group, such as Network Speaker under Type. */
export type Facet = { group: string; value: string };

/** What a row says of itself and offers; the tree draws it and decides nothing. */
export type Row = {
  id: string;
  label: string;
  /** A short chip before the label: how the stream's overlay arrives. */
  tag: string;
  /** Muted text after the label, right-aligned; it ends in an ellipsis before the label does. */
  aside: string;
  /** Muted text under the label. */
  detail: string;
  /** Whether the detail is drawn in the caution tone. */
  detailWarning: boolean;
  /** Muted lines shown under the row while a click on it has it opened. */
  info: readonly string[];
  /** The hover text. */
  title: string;
  mark: { icon: IconName; color: MarkColor };
  /** Whether the label is dimmed: the thing is not running. */
  dim: boolean;
  /** Whether the row is open in a viewer. */
  active: boolean;
  /** The button at the row's right; also the first entry of its menu. */
  action: RowAction | null;
  /** Everything the row's menu offers, in order. */
  menu: readonly RowAction[];
  /** Where the hover card's picture is, else `null`. */
  thumbnail: string | null;
  /** What the search matches: the tag, label, aside, detail and the terms of what a scan learned. */
  search: string;
  facets: readonly Facet[];
  /** What the scan learned of the camera, which the Sort reads. */
  scan: NetworkRow | null;
  children: readonly Row[];
  /** For a stream waiting for the user to confirm references. */
  confirm: readonly LiveConfirmation[];
  /** What the action acts on: the stream's item, the item to copy for another stream, or the found camera. */
  subject: { item?: string; like?: string; found?: NetworkRow };
};

/** A titled group of rows, which is always listed when it has an Add stream button or something to say when empty. */
export type Section = {
  id: "live" | "network";
  title: string;
  rows: readonly Row[];
  /** Whether the caption's mark is in its kind's colour, else muted. */
  lit: boolean;
  /** Whether the caption has the Add stream button. */
  adds: boolean;
  /** What the section says under its caption while it has no rows. */
  empty: string;
};

/** What a found camera's row says on hover when it has no Add. */
export const NOT_SIGNED_IN = "Not signed in: add a login under Try to sign in.";
/** What a stream's row says when its own login does not get in. */
export const REFUSED = "The camera refused the login saved with it.";

const TAGS = { none: "", rtsp: "RTSP", mqtt: "MQTT", datahub: "WS" } as const;
const PROBLEMS: ReadonlySet<string> = new Set(["failed", "reconnecting", "stalled"]);
const dotted = (...parts: readonly (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(" · ");

/** The non-secret details the hover text lists of what a scan learned; a sign-in that did not work is told when asked. */
function details(row: NetworkRow, signInProblem: boolean): string[] {
  const { identity, sign_in, uptime_s } = row;
  const signed = sign_in.state === "SignedIn" ? sign_in : null;
  return [
    identity.model,
    signInProblem && (sign_in.state === "Refused" || sign_in.state === "Failed")
      ? SIGN_IN[sign_in.state].label
      : "",
    identity.serial && `Serial ${identity.serial}`,
    identity.firmware && `Firmware ${identity.firmware}`,
    identity.product_type && `Type ${identity.product_type}`,
    uptime_s !== null && `Up ${uptimeText(uptime_s)}`,
    signed?.architecture && `Architecture ${signed.architecture}`,
    signed?.soc && `SoC ${signed.soc}`,
  ].filter((fact): fact is string => Boolean(fact));
}

/** The lines a camera's row opens: where it is and what it is, then its serial number and uptime. */
function cameraInfo(host: string, scan: NetworkRow | null): string[] {
  if (!scan) return [host];
  const { identity, uptime_s } = scan;
  return [
    dotted(host, identity.product_type, identity.firmware && `Firmware ${identity.firmware}`),
    dotted(
      identity.serial && `Serial ${identity.serial}`,
      uptime_s !== null && `Up ${uptimeText(uptime_s)}`,
    ),
  ].filter(Boolean);
}

/** What the search finds a camera's row by besides what it shows. */
function searchTerms(host: string, scan: NetworkRow | null): string {
  if (!scan) return host;
  const { identity, sign_in } = scan;
  const platform = sign_in.state === "SignedIn" && dotted(sign_in.architecture, sign_in.soc);
  return dotted(
    host,
    identity.model,
    identity.serial,
    identity.firmware,
    identity.product_type,
    platform,
  );
}

const facetsOf = (scan: NetworkRow): Facet[] => [
  { group: "Type", value: scan.identity.product_type || "Unknown" },
  { group: "Sign-in", value: SIGN_IN[scan.sign_in.state].label },
];

/** The key the Filter keeps a facet by. */
export const facetKey = ({ group, value }: Facet) => `${group}\n${value}`;

const searchText = (row: Pick<Row, "tag" | "label" | "aside" | "detail">, terms: string) =>
  [row.tag, row.label, row.aside, row.detail, terms].filter(Boolean).join(" ");

const BASE: Omit<Row, "id" | "label" | "mark" | "subject"> = {
  tag: "",
  aside: "",
  detail: "",
  detailWarning: false,
  info: [],
  title: "",
  dim: false,
  active: false,
  action: null,
  menu: [],
  thumbnail: null,
  search: "",
  facets: [],
  scan: null,
  children: [],
  confirm: [],
};

/** What a stream's row calls it: the topic or data source its overlay draws, else the decoder, else what it is. */
function streamLabel({ overlay, recipe, lane, label, state }: Stream): string {
  if (state === "confirm") return "Needs confirmation";
  if (state !== "ready" && !recipe) return label;
  const draws = recipe?.overlay;
  if (overlay === "none" || draws?.mode === "none") return "Video only";
  const source =
    draws?.mode === "datahub" ? draws.topic : draws?.mode === "mqtt" ? draws.source_key : "";
  return source || draws?.handler || lane || "Video only";
}

/** The lines a stream's row opens: the MQTT broker, else where the stream comes from. */
function streamInfo({ recipe }: Stream): string[] {
  const draws = recipe?.overlay;
  const broker = draws?.mode === "mqtt" ? draws.broker_host?.trim() : "";
  const where = broker
    ? `${broker}${draws?.mode === "mqtt" && draws.broker_port ? `:${draws.broker_port}` : ""}`
    : recipe?.host?.trim();
  return where ? [where] : [];
}

/** Whether the camera refused the login saved with the stream, as its own check found. */
const refuses = (check: NetworkRow | undefined): boolean => check?.sign_in.state === "Refused";

/**
 * How a stream is doing: a stream that did not resolve or whose login the camera refuses has failed; one not open is
 * idle, and an open one as its viewer last reported.
 */
function statusOfStream(
  stream: Stream,
  reported: ReadonlyMap<string, StreamStatus>,
  check: NetworkRow | undefined,
  front: string | null,
): StreamStatus {
  if (refuses(check)) return { state: "failed", text: REFUSED, anyLive: false };
  if (stream.state === "failed" || stream.state === "confirm")
    return { state: "failed", text: stream.reason, anyLive: false };
  if (stream.state === "resolving") return IDLE;
  return running(stream, front) ? (reported.get(stream.id) ?? CONNECTING) : IDLE;
}

/** What the menu of a stream offers: Start or Stop when it can, then Information and Remove. */
const menuOf = (action: RowAction | null, informs: boolean): RowAction[] => [
  ...(action ? [action] : []),
  ...(informs ? (["information"] as const) : []),
  "remove",
];

/** The row of a stream that is not ready: resolving, failed, or waiting for a confirmation. */
function unreadyRow(stream: Stream, label: string): Row {
  const waiting = stream.state === "resolving";
  const detail = waiting ? "" : stream.reason;
  return {
    ...BASE,
    id: stream.id,
    label,
    detail,
    detailWarning: detail !== "",
    mark: waiting
      ? { icon: "video", color: "muted" }
      : { icon: "triangle-alert", color: "caution" },
    dim: waiting,
    menu: menuOf(null, !waiting),
    confirm: stream.confirm,
    search: searchText({ ...BASE, label, detail }, ""),
    subject: { item: stream.id },
  };
}

function streamRow(
  stream: Stream,
  status: StreamStatus,
  check: NetworkRow | undefined,
  thumbnails: boolean,
  front: string | null,
): Row {
  const label = streamLabel(stream);
  const on = running(stream, front);
  if (stream.state !== "ready") return unreadyRow(stream, label);
  const refused = refuses(check);
  const tag = TAGS[stream.overlay ?? "none"];
  const problem = refused ? REFUSED : PROBLEMS.has(status.state) ? status.text : "";
  // A stream whose login is refused cannot start, but can still be stopped.
  const action: RowAction | null = on ? "stop" : refused ? null : "start";
  return {
    ...BASE,
    id: stream.id,
    label,
    tag,
    detail: problem,
    detailWarning: problem !== "",
    info: streamInfo(stream),
    title: [
      `Live stream${stream.overlay === "none" ? "" : " · 1 overlay"}`,
      refused ? REFUSED : status.text,
      on ? "Open in a viewer" : "",
    ]
      .filter(Boolean)
      .join("\n"),
    mark: refused
      ? { icon: "triangle-alert", color: "caution" }
      : {
          icon: tag ? "layers" : "circle-play",
          color: on ? MARKS[status.state].color : "muted",
        },
    dim: !on && !refused,
    active: on,
    action,
    menu: menuOf(action, true),
    thumbnail: thumbnails && !refused ? `/api/live/snapshot/${stream.id}` : null,
    search: searchText({ tag, label, aside: "", detail: problem }, ""),
    subject: { item: stream.id },
  };
}

/** A camera the workspace streams, with its streams under it; `scan` is what a scan learned of it. */
function cameraRow(
  camera: Camera,
  scan: NetworkRow | null,
  reported: ReadonlyMap<string, StreamStatus>,
  checks: Checks,
  thumbnails: boolean,
  front: string | null,
): { row: Row; status: StreamStatus } {
  const statuses = camera.streams.map((stream) =>
    statusOfStream(stream, reported, checks.get(stream.id), front),
  );
  const status = mostUrgent(statuses);
  const idle = status.state === "idle" && !camera.streams.some((stream) => running(stream, front));
  // What signing in with a stream's own login found tells more than a scan that did not sign in with it.
  const learned = camera.streams.map(({ id }) => checks.get(id)).find((check) => check) ?? scan;
  const aside = learned?.identity.model || status.text || "Stopped";
  const host = camera.address;
  const children = camera.streams.map((stream, at) =>
    streamRow(stream, statuses[at] ?? IDLE, checks.get(stream.id), thumbnails, front),
  );
  const row: Row = {
    ...BASE,
    id: `camera/${camera.key}`,
    label: camera.name,
    aside,
    info: cameraInfo(host, learned),
    title: ["Camera", ...(learned ? details(learned, false) : []), status.text]
      .filter(Boolean)
      .join("\n"),
    mark: { icon: MARKS[status.state].icon, color: idle ? "muted" : MARKS[status.state].color },
    dim: idle,
    action: "addStream",
    menu: ["addStream"],
    thumbnail: children.find((child) => child.thumbnail)?.thumbnail ?? null,
    search: searchText(
      { tag: "", label: camera.name, aside, detail: "" },
      searchTerms(host, learned),
    ),
    facets: learned ? facetsOf(learned) : [],
    scan: learned,
    children,
    subject: { like: camera.streams.find(({ recipe }) => recipe)?.id },
  };
  return { row, status };
}

/** A camera the scan found: its model, or else whether the scan signed in, beside the address; Add once signed in. */
function foundRow(found: NetworkRow, thumbnails: boolean): Row {
  const signedIn = found.sign_in.state === "SignedIn";
  const aside = found.identity.model || SIGN_IN[found.sign_in.state].label;
  return {
    ...BASE,
    id: `network/${found.address}`,
    label: found.address,
    aside,
    info: cameraInfo(found.address, found),
    title: ["Camera on the network", ...details(found, true), signedIn ? "" : NOT_SIGNED_IN]
      .filter(Boolean)
      .join("\n"),
    mark: { icon: "video", color: "muted" },
    dim: !signedIn,
    action: signedIn ? "add" : null,
    menu: signedIn ? ["add"] : [],
    thumbnail:
      signedIn && thumbnails
        ? `/api/live/snapshot/network/${encodeURIComponent(found.address)}`
        : null,
    search: searchText(
      { tag: "", label: found.address, aside, detail: "" },
      searchTerms(found.address, found),
    ),
    facets: facetsOf(found),
    scan: found,
    subject: { found },
  };
}

/** The rows of `rows` in `order`; the rows under a row keep theirs, and equal rows keep the order given. */
function sorted(rows: readonly Row[], order: Order): Row[] {
  return [...rows].sort(
    (a, b) =>
      compareScans(order, a.scan, b.scan) ||
      (order === "name" ? a.label.localeCompare(b.label, undefined, { sensitivity: "base" }) : 0),
  );
}

/**
 * The Live view's sections: the workspace's cameras (always listed, with the Add stream button), then, once a scan has
 * started, the cameras it found that the workspace holds no stream of.
 */
export function sectionsOf({
  cameras,
  found,
  scanned,
  reported,
  checks,
  thumbnails,
  front,
  order,
}: {
  cameras: readonly Camera[];
  found: readonly NetworkRow[];
  /** Whether a scan has started, which is when the cameras it finds are listed. */
  scanned: boolean;
  reported: ReadonlyMap<string, StreamStatus>;
  /** What signing in with each stream's own login found. */
  checks: Checks;
  /** Whether rows offer a hover card with a picture of their camera. */
  thumbnails: boolean;
  /** The item whose viewer is in front. */
  front: string | null;
  order: Order;
}): Section[] {
  const byHost = new Map(found.map((row) => [row.address.toLowerCase(), row]));
  const held = cameras.map((camera) =>
    cameraRow(
      camera,
      (camera.host && byHost.get(camera.host)) || null,
      reported,
      checks,
      thumbnails,
      front,
    ),
  );
  const live: Section = {
    id: "live",
    title: "Live",
    rows: sorted(
      held.map(({ row }) => row),
      order,
    ),
    lit: held.some(({ status }) => status.anyLive),
    adds: true,
    empty: "",
  };
  if (!scanned) return [live];
  const hosts = new Set(cameras.flatMap(({ host }) => (host ? [host] : [])));
  const network: Section = {
    id: "network",
    title: "On the network",
    rows: sorted(
      found
        .filter((row) => !hosts.has(row.address.toLowerCase()))
        .map((row) => foundRow(row, thumbnails)),
      order,
    ),
    lit: false,
    adds: false,
    empty: "No new cameras",
  };
  return [live, network];
}

/** A row the filter shows, with the rows under it that it shows. */
export type Shown = { row: Row; children: Shown[] };

/**
 * The rows of `rows` a filter shows: a row `allowed` passes, and, when searching, one that matches, sits under a match
 * or holds one.
 */
export function shownRows(
  rows: readonly Row[],
  allowed: (row: Row) => boolean,
  matches: ((row: Row) => boolean) | null,
  ancestorMatches = false,
): Shown[] {
  return rows.flatMap((row) => {
    const own = matches === null || matches(row);
    const children = shownRows(row.children, allowed, matches, ancestorMatches || own);
    return allowed(row) && (matches === null || ancestorMatches || own || children.length > 0)
      ? [{ row, children }]
      : [];
  });
}

/** How many rows are in `shown`, the rows under them included. */
export const countShown = (shown: readonly Shown[]): number =>
  shown.reduce((sum, { children }) => sum + 1 + countShown(children), 0);

/** The facets of `sections`' rows that the Filter offers: those of a group with a choice to make, or one the user hid. */
export function offeredFacets(sections: readonly Section[], hidden: ReadonlySet<string>): Facet[] {
  const all = new Map<string, Facet>();
  const visit = (rows: readonly Row[]) =>
    rows.forEach((row) => {
      row.facets.forEach((facet) => all.set(facetKey(facet), facet));
      visit(row.children);
    });
  sections.forEach(({ rows }) => visit(rows));
  const groups = new Map<string, Facet[]>();
  for (const facet of [...all.values()].sort(
    (a, b) => a.group.localeCompare(b.group) || a.value.localeCompare(b.value),
  ))
    groups.set(facet.group, [...(groups.get(facet.group) ?? []), facet]);
  return [...groups.values()]
    .filter((group) => group.length > 1 || group.some((facet) => hidden.has(facetKey(facet))))
    .flat();
}

/** What the filter and the search make of a section: the rows shown, how many the Filter alone leaves out, and whether it is listed. */
export type SectionView = {
  section: Section;
  shown: Shown[];
  /** Rows a search would show that the Filter hides. */
  filteredOut: number;
  /** A section with nothing shown is hidden unless it has the Add stream button, has an empty text to say, or has rows the Filter hides. */
  listed: boolean;
  searched: boolean;
};

/**
 * `sections` as the search `query` and the facets the user `hidden` show them. The query matches what a row says of
 * itself, upper or lower case.
 */
export function viewsOf(
  sections: readonly Section[],
  query: string,
  hidden: ReadonlySet<string>,
): SectionView[] {
  const text = query.trim().toLowerCase();
  const matches = text ? (row: Row) => row.search.toLowerCase().includes(text) : null;
  const allowed = (row: Row) => !row.facets.some((facet) => hidden.has(facetKey(facet)));
  return sections.map((section) => {
    const shown = shownRows(section.rows, allowed, matches);
    const filteredOut =
      countShown(shownRows(section.rows, () => true, matches)) - countShown(shown);
    const listed =
      shown.length > 0 ||
      filteredOut > 0 ||
      section.adds ||
      (section.rows.length === 0 && section.empty !== "");
    return { section, shown, filteredOut, listed, searched: shown.length > 0 && matches !== null };
  });
}
