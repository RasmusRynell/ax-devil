import type {
  HandlersInfo,
  KeptDataHubWire,
  KeptMqttWire,
  LiveConnection,
  LiveProtocol,
  LiveRtspRoute,
  SettingsState,
  NewLiveOverlayWire,
  NewLiveWire,
} from "../generated/api";
import { SETTINGS, type SettingKey } from "../generated/settings";
import { isReference } from "../client/secret";
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
  /** Blank is the configured route. */
  rtsp_route: "" | LiveRtspRoute;
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
  rtsp_route: true,
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
export type TextKey = Exclude<FieldKey, "protocol" | "rtsp_route" | "overlay">;

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

/**
 * `settings` with each valued one that holds a `$NAME` reference replaced by what the reference gives, or by the
 * setting's default for a caller the backend does not tell (a remote password holder), so no reference is ever
 * prefilled into a typed field.
 */
export function formValues(settings: SettingsState): SettingsState["values"] {
  const given = settings.resolved ?? {};
  const replaced = VALUED.flatMap((key) => {
    const raw = settings.values[key];
    if (typeof raw !== "string" || !isReference(raw)) return [];
    const kind = SETTINGS.find((row) => row.key === key)?.kind;
    return [[key, given[key] ?? (kind && "default" in kind ? kind.default : "")]];
  });
  return { ...settings.values, ...Object.fromEntries(replaced) };
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
    rtsp_route: "",
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

/** The text of a wire value that is there, empty or 0 included, else `fallback`. */
const held = (value: string | number | undefined, fallback: string) =>
  value === undefined ? fallback : String(value);

/** The empty form with the fields of `wire` filled in, as the form would read back to `wire`. */
export function formFrom(values: SettingsState["values"], wire: Partial<NewLiveWire>): LiveForm {
  const form = startForm(values);
  const overlay = wire.overlay;
  // A mode not chosen shows what the stream kept for it.
  const mqtt = overlay?.mode === "mqtt" ? overlay : wire.kept_mqtt;
  const datahub = overlay?.mode === "datahub" ? overlay : wire.kept_datahub;
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
    rtsp_route: wire.rtsp_route ?? "",
    resolution: shown(wire.resolution),
    url: shown(wire.url),
    overlay: overlay?.mode ?? form.overlay,
    handler: overlay && overlay.mode !== "none" ? shown(overlay.handler) : "",
    // A value the stream holds is shown as it is, empty or 0 included; only one it lacks takes the default.
    ...(mqtt && {
      broker_host: shown(mqtt.broker_host),
      broker_user: shown(mqtt.broker_user),
      broker_pass: shown(mqtt.broker_pass),
      broker_port: held(mqtt.broker_port, form.broker_port),
      source_key: held(mqtt.source_key, form.source_key),
    }),
    ...(datahub && {
      topic: held(datahub.topic, form.topic),
      channel: held(datahub.channel, form.channel),
    }),
  };
}

/** The data source and topic `wire` saved, whether their mode is the overlay or kept. */
export function savedChoices(wire: Partial<NewLiveWire>): Record<"mqtt" | "datahub", string> {
  const { overlay } = wire;
  return {
    mqtt: (overlay?.mode === "mqtt" ? overlay : wire.kept_mqtt)?.source_key ?? "",
    datahub: (overlay?.mode === "datahub" ? overlay : wire.kept_datahub)?.topic ?? "",
  };
}

/**
 * The password fields of an edited stream whose saved password is a literal, which the client is never sent: a blank
 * one keeps it. A reference or an empty password comes in `wire` and shows as it is.
 */
export function hiddenSecrets(wire: Partial<NewLiveWire>): FieldKey[] {
  const mqtt = wire.overlay?.mode === "mqtt" ? wire.overlay : wire.kept_mqtt;
  return [
    ...(wire.host !== undefined && wire.password === undefined ? ["password" as const] : []),
    ...(mqtt && mqtt.broker_pass === undefined ? ["broker_pass" as const] : []),
  ];
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
  if (head !== undefined && head > 9999) return "Camera Head must be at most 9999.";
  const rtsp = whole(form.rtsp_port);
  if (rtsp !== undefined && (rtsp < 1 || rtsp > 65535))
    return "RTSP Port must be between 1 and 65535.";
  // The standard port of the other protocol cannot be addressed.
  const protocol = form.protocol || textOf(values["camera.protocol"]);
  if (whole(form.port) === (protocol === "http" ? 443 : protocol === "https" ? 80 : undefined))
    return `${protocol.toUpperCase()} cannot use port ${form.port.trim()}; use the standard port.`;
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

/**
 * The camera a question is about. Blank fields are the configured ones, except in an edit, where the login is sent as
 * typed so a cleared one is asked with, as the stream is saved with it.
 */
export function connectionOf(form: LiveForm, head: boolean, editing = false): LiveConnection {
  return {
    host: text(form.host),
    username: editing ? form.username : text(form.username),
    password: editing ? form.password : text(form.password),
    protocol: form.protocol || undefined,
    port: whole(form.port),
    head: head ? whole(form.head) : undefined,
  };
}

/** How a text is read from the form: an addition trims it and drops a blank; an edit sends it as typed, empty included. */
type Read = (entered: string) => string | undefined;

const mqttOf = (form: LiveForm, read: Read): KeptMqttWire => ({
  broker_host: read(form.broker_host),
  broker_port: whole(form.broker_port),
  broker_user: read(form.broker_user),
  broker_pass: read(form.broker_pass),
  source_key: read(form.source_key),
});

const datahubOf = (form: LiveForm, read: Read): KeptDataHubWire => ({
  topic: read(form.topic),
  channel: whole(form.channel),
});

function overlayOf(form: LiveForm, read: Read): NewLiveOverlayWire {
  const handler = read(form.handler);
  switch (form.overlay) {
    case "none":
      return { mode: "none" };
    case "rtsp":
      return { mode: "rtsp", handler };
    case "mqtt":
      return { mode: "mqtt", handler, ...mqttOf(form, read) };
    case "datahub":
      return { mode: "datahub", handler, ...datahubOf(form, read) };
  }
}

/**
 * What the form adds, or with `editing` what replaces the stream. The fields of the chosen mode are the overlay and
 * those of the others are kept with the stream. An addition sends nothing for a blank text, which the backend
 * defaults; an edit sends every text as typed, empty included, so a cleared field stays cleared.
 */
export function newLive(form: LiveForm, editing = false): NewLiveWire {
  const read: Read = editing ? (entered) => entered : text;
  return {
    label: read(form.label),
    host: read(form.host),
    username: read(form.username),
    password: read(form.password),
    protocol: form.protocol || undefined,
    port: whole(form.port),
    head: whole(form.head),
    rtsp_port: whole(form.rtsp_port),
    rtsp_route: form.rtsp_route || undefined,
    resolution: read(form.resolution),
    url: read(form.url),
    overlay: overlayOf(form, read),
    kept_mqtt: form.overlay === "mqtt" ? undefined : mqttOf(form, read),
    kept_datahub: form.overlay === "datahub" ? undefined : datahubOf(form, read),
  };
}
