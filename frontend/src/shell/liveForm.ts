import type {
  HandlersInfo,
  LiveConnection,
  LiveProtocol,
  SettingsState,
  NewLiveOverlayWire,
  NewLiveWire,
} from "../generated/api";
import type { SettingKey } from "../generated/settings";
import { textOf } from "./SettingField";

/** The overlay modes, in the order the `camera.overlay` setting lists them. */
export type OverlayMode = "none" | "rtsp" | "mqtt" | "datahub";

/** What each mode does, as its page of the dialog says it. */
export const OVERLAY_TEXT: Record<OverlayMode, string> = {
  none: "Show the video only. Choose a mode to draw the device's analytics on it.",
  rtsp: "Decode analytics metadata sent inside the RTSP video stream.",
  mqtt: "Receive analytics that the device publishes to an MQTT broker.",
  datahub: "Subscribe to a DataHub analytics topic on the device.",
};

/**
 * What the user typed, one text per field and named like the backend names the field in a refusal. A blank text takes
 * the configured default; modes keep their fields while another is chosen.
 */
export interface LiveForm {
  label: string;
  host: string;
  username: string;
  password: string;
  /** Blank is the configured protocol. */
  protocol: "" | LiveProtocol;
  port: string;
  head: string;
  rtsp_port: string;
  resolution: string;
  url: string;
  overlay: OverlayMode;
  /** The decoder every mode but `none` needs; blank is none chosen. */
  handler: string;
  broker_host: string;
  broker_user: string;
  broker_pass: string;
  broker_port: string;
  source_key: string;
  topic: string;
  channel: string;
}

export type FieldKey = keyof LiveForm;

const FIELDS: Record<FieldKey, true> = {
  label: true,
  host: true,
  username: true,
  password: true,
  protocol: true,
  port: true,
  head: true,
  rtsp_port: true,
  resolution: true,
  url: true,
  overlay: true,
  handler: true,
  broker_host: true,
  broker_user: true,
  broker_pass: true,
  broker_port: true,
  source_key: true,
  topic: true,
  channel: true,
};

/** Whether the backend's `location` of a refusal names a field of this form. */
export const isField = (location: string | undefined): boolean =>
  location !== undefined && Object.hasOwn(FIELDS, location);
/** The fields that hold any text. */
export type TextKey = Exclude<FieldKey, "protocol" | "overlay">;

/** The settings the form starts from as values, unlike the host and logins, which stay `$NAME` references. */
const VALUED: readonly SettingKey[] = [
  "camera.head",
  "camera.overlay",
  "camera.rtsp_handler",
  "broker.port",
  "broker.source_key",
  "broker.handler",
  "datahub.topic",
  "datahub.channel",
  "datahub.handler",
];

/** `settings` with the valued ones replaced by what their `$NAME` references give. */
export function formValues(settings: SettingsState): SettingsState["values"] {
  const given = settings.resolved ?? {};
  return {
    ...settings.values,
    ...Object.fromEntries(VALUED.flatMap((key) => (key in given ? [[key, given[key]]] : []))),
  };
}

/** The setting that names the decoder of each mode that has one. */
const HANDLER_OF: Partial<Record<OverlayMode, SettingKey>> = {
  rtsp: "camera.rtsp_handler",
  mqtt: "broker.handler",
  datahub: "datahub.handler",
};

/**
 * The overlay the settings choose and the decoder they name for it, when that decoder can read a live stream;
 * otherwise nothing, and the form stays on no overlay.
 */
export function configuredOverlay(
  values: SettingsState["values"],
  decoders: HandlersInfo["decoders"],
): { overlay: OverlayMode; handler: string } | undefined {
  const overlay = textOf(values["camera.overlay"]) as OverlayMode;
  const key = HANDLER_OF[overlay];
  const handler = key ? textOf(values[key]) : "";
  return decoders.some((decoder) => decoder.live && decoder.handler === handler)
    ? { overlay, handler }
    : undefined;
}

/**
 * The empty form: the numbers, the data source and the topic the settings give as the dialog's own text, and no overlay
 * until the decoders are known. The data source and topic count only once the camera lists them.
 */
export function startForm(values: SettingsState["values"]): LiveForm {
  return {
    label: "",
    host: "",
    username: "",
    password: "",
    protocol: "",
    port: "",
    head: textOf(values["camera.head"]),
    rtsp_port: "",
    resolution: "",
    url: "",
    overlay: "none",
    handler: "",
    broker_host: "",
    broker_user: "",
    broker_pass: "",
    broker_port: textOf(values["broker.port"]),
    source_key: textOf(values["broker.source_key"]),
    topic: textOf(values["datahub.topic"]),
    channel: textOf(values["datahub.channel"]),
  };
}

/** The text of a wire value; blank when absent or 0, which is the default for every number here. */
const shown = (value: string | number | undefined) => (value ? String(value) : "");

/** The empty form with the fields of `wire` filled in, as the form would read back to `wire`. */
export function formFrom(values: SettingsState["values"], wire: Partial<NewLiveWire>): LiveForm {
  const form = startForm(values);
  const overlay = wire.overlay;
  return {
    ...form,
    label: shown(wire.label),
    host: shown(wire.host),
    username: shown(wire.username),
    password: shown(wire.password),
    protocol: wire.protocol ?? "",
    port: shown(wire.port),
    head: shown(wire.head) || form.head,
    rtsp_port: shown(wire.rtsp_port),
    resolution: shown(wire.resolution),
    url: shown(wire.url),
    overlay: overlay?.mode ?? form.overlay,
    handler: overlay && overlay.mode !== "none" ? shown(overlay.handler) : "",
    ...(overlay?.mode === "mqtt" && {
      broker_host: shown(overlay.broker_host),
      broker_user: shown(overlay.broker_user),
      broker_pass: shown(overlay.broker_pass),
      broker_port: shown(overlay.broker_port) || form.broker_port,
      source_key: shown(overlay.source_key) || form.source_key,
    }),
    ...(overlay?.mode === "datahub" && {
      topic: shown(overlay.topic) || form.topic,
      channel: shown(overlay.channel) || form.channel,
    }),
  };
}

/** The numbers that are plain text, with what to say when one is not a whole number. */
const NUMBERS = {
  broker_port: "MQTT Port must be an integer.",
  channel: "Topic Channel must be an integer.",
} as const;

const text = (entered: string) => entered.trim() || undefined;

/** The whole number a field holds, nothing when blank or when it is something else. */
const whole = (entered: string) => (/^\d+$/.test(entered.trim()) ? Number(entered) : undefined);

/** The Camera Head the stream will use: the field, else the configured one, else 1. */
export function headOf(form: LiveForm, configured: string): number {
  return whole(form.head) ?? whole(configured) ?? 1;
}

/**
 * Whether `entered` can still become a whole number up to `max`; the fields that take a bounded number accept
 * nothing else, so they are never misread.
 */
export const typable = (entered: string, max: number) =>
  /^\d*$/.test(entered) && (entered === "" || Number(entered) <= max);

/** The choice `value` of a list the camera gave: the value itself while the list has it, else none. */
export const chosen = (value: string, choices: readonly string[]) =>
  choices.includes(value) ? value : "";

/** What is wrong with a stream URL, as the backend's recipe check says it, or nothing. */
function urlProblem(entered: string) {
  const lower = entered.trim().toLowerCase();
  if (!lower) return undefined;
  if (!lower.startsWith("rtsp://")) return "The stream URL must start with rtsp://.";
  const authority = lower.slice("rtsp://".length).split(/[/?#]/)[0] ?? "";
  if (authority.includes("@")) return "The stream URL must not contain a login.";
  const host = authority.startsWith("[")
    ? /^\[[^\]]*\]/.exec(authority)?.[0]
    : authority.split(":")[0];
  return host ? undefined : "The stream URL has no host.";
}

/**
 * What stops the form from being added, in the order the form is read, or nothing: the rules the backend's recipe
 * check applies, so OK waits for a form the backend accepts. Every number is read whatever the overlay, so a saved
 * value of a mode not chosen still counts. A blank host or broker host takes the configured one in `values`; the data
 * source and topic must be chosen, so `form` holds them as the lists give them (see `chosen`).
 */
export function problemOf(form: LiveForm, values: SettingsState["values"], modeName: string) {
  if (!(form.host.trim() || textOf(values["camera.host"]).trim())) return "Enter the device host.";
  if (form.overlay !== "none" && !form.handler)
    return `Select a handler type for ${modeName} overlays.`;
  const misread = (Object.keys(NUMBERS) as (keyof typeof NUMBERS)[]).find(
    (key) => !/^\d+$/.test(form[key].trim()),
  );
  if (misread) return NUMBERS[misread];
  const head = whole(form.head);
  if (head !== undefined && head < 1) return "Camera Head must be a positive integer.";
  if (head !== undefined && head > 255) return "Camera Head must be at most 255.";
  // The standard port of the other protocol cannot be addressed.
  const protocol = form.protocol || textOf(values["camera.protocol"]);
  if (whole(form.port) === (protocol === "http" ? 443 : protocol === "https" ? 80 : undefined))
    return `${protocol.toUpperCase()} cannot use port ${form.port.trim()}; use the standard port.`;
  const url = urlProblem(form.url);
  if (url) return url;
  const broker = Number(form.broker_port);
  const channel = Number(form.channel);
  switch (form.overlay) {
    case "mqtt":
      if (!(form.broker_host.trim() || textOf(values["broker.host"]).trim()))
        return "MQTT Host is required for MQTT overlay mode.";
      if (!form.source_key.trim()) return "Data Source is required for MQTT overlay mode.";
      if (broker < 1 || broker > 65535) return "MQTT Port must be between 1 and 65535.";
      return undefined;
    case "datahub":
      if (!form.topic.trim()) return "Topic is required for WebSocket overlay mode.";
      if (channel < 1) return "Channel ID must be a positive integer.";
      if (channel > 4294967295) return "Channel ID must be at most 4294967295.";
      return undefined;
    default:
      return undefined;
  }
}

/** The camera a question is about. Blank fields are the configured ones. */
export function connectionOf(form: LiveForm, head: boolean): LiveConnection {
  return {
    host: text(form.host),
    username: text(form.username),
    password: text(form.password),
    protocol: form.protocol || undefined,
    port: whole(form.port),
    head: head ? whole(form.head) : undefined,
  };
}

function overlayOf(form: LiveForm): NewLiveOverlayWire {
  const handler = text(form.handler);
  switch (form.overlay) {
    case "none":
      return { mode: "none" };
    case "rtsp":
      return { mode: "rtsp", handler };
    case "mqtt":
      return {
        mode: "mqtt",
        handler,
        broker_host: text(form.broker_host),
        broker_port: whole(form.broker_port),
        broker_user: text(form.broker_user),
        broker_pass: text(form.broker_pass),
        source_key: text(form.source_key),
      };
    case "datahub":
      return { mode: "datahub", handler, topic: text(form.topic), channel: whole(form.channel) };
  }
}

/** What the form adds: only the fields of the chosen mode, and nothing for a blank text, which the backend defaults. */
export function newLive(form: LiveForm): NewLiveWire {
  return {
    label: text(form.label),
    host: text(form.host),
    username: text(form.username),
    password: text(form.password),
    protocol: form.protocol || undefined,
    port: whole(form.port),
    head: whole(form.head),
    rtsp_port: whole(form.rtsp_port) || undefined,
    resolution: text(form.resolution),
    url: text(form.url),
    overlay: overlayOf(form),
  };
}
