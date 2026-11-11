import type { ReactNode } from "react";
import type {
  HandlersInfo,
  LiveExpansions,
  LiveResolutions,
  SettingsState,
} from "../generated/api";
import { SETTINGS, type SettingKey } from "../generated/settings";
import type { Asked } from "../live/discovery";
import { referenceIn, SET_PASSWORD, viaText } from "../live/expansions";
import { Button } from "../kit/Button";
import { Form, FormCombo, FormInput, FormNote, FormSelect, FormStack } from "../kit/Form";
import {
  chosen,
  OVERLAY_TEXT,
  typable,
  type FieldKey,
  type LiveForm,
  type OverlayMode,
  type TextKey,
} from "./liveForm";
import { literalSecret, textOf, type Row } from "./SettingField";

const ROWS: readonly Row[] = SETTINGS;
const rowOf = (key: SettingKey) => ROWS.find((row) => row.key === key);

/** The setting that gives a blank field its value. */
const DEFAULTS: Partial<Record<FieldKey, SettingKey>> = {
  host: "camera.host",
  username: "camera.user",
  password: "camera.password",
  rtsp_port: "camera.rtsp_port",
  broker_host: "broker.host",
  broker_user: "broker.user",
  broker_pass: "broker.password",
};

/** What every group of fields reads and changes. */
export interface PanelProps {
  form: LiveForm;
  set: <K extends FieldKey>(field: K, value: LiveForm[K]) => void;
  /** The configured values, by setting key. */
  settings: SettingsState["values"];
  /** What the references of the configured defaults show; `null` until the backend has said, or where it may not. */
  expansions: LiveExpansions | null;
  /** In an edit, the password fields whose saved literal is kept while they are blank; `null` when adding. */
  hidden: readonly FieldKey[] | null;
}

/** What the backend said a default reference expands to, per field: `null` when it did not say. */
const EXPANDED: Partial<Record<FieldKey, (shown: LiveExpansions) => string | null>> = {
  host: (shown) => shown.host,
  username: (shown) => shown.username,
  password: (shown) =>
    shown.password_set === null ? null : shown.password_set ? SET_PASSWORD : "",
  broker_host: (shown) => shown.broker_host,
  broker_user: (shown) => shown.broker_user,
  broker_pass: (shown) =>
    shown.broker_pass_set === null ? null : shown.broker_pass_set ? SET_PASSWORD : "",
};

/**
 * What a blank `field` takes, as the placeholder shows it: a literal default as it is (a secret as dots), and a
 * `$NAME` reference as its value followed by `(via $NAME)` where the backend says it may be expanded (a password as
 * dots, its length not told), else as the reference. An edit takes no default: a blank field is blank, except a saved
 * password, which dots show is kept.
 */
function hint(props: PanelProps, field: FieldKey) {
  if (props.hidden) return props.hidden.includes(field) ? SET_PASSWORD : undefined;
  const key = DEFAULTS[field];
  const row = key && rowOf(key);
  if (!key || !row) return undefined;
  const value = props.settings[key];
  const shown = textOf(value);
  if (row.secret && literalSecret(row, value)) return SET_PASSWORD;
  const expanded = props.expansions && EXPANDED[field]?.(props.expansions);
  return viaText(shown, expanded);
}

/** What the Name placeholder says: the host the stream will have, which is the host in the form or else the default. */
function nameHint({ form, settings, expansions }: PanelProps) {
  const host = form.host.trim() || textOf(settings["camera.host"]).trim();
  if (referenceIn(host) === null) return host || undefined;
  if (!expansions) return undefined;
  return expansions.host === null ? "Live stream" : expansions.host || undefined;
}

function input(
  props: PanelProps,
  field: TextKey,
  label: string,
  more: { placeholder?: string; masked?: boolean; title?: string; selectOnOpen?: boolean } = {},
) {
  return (
    <FormInput
      label={label}
      value={props.form[field]}
      placeholder={more.placeholder ?? hint(props, field)}
      masked={more.masked}
      selectOnOpen={more.selectOnOpen}
      title={more.title}
      onChange={(value) => props.set(field, value)}
    />
  );
}

/** A number box that takes digits up to `limit` only. */
function number(
  props: PanelProps,
  field: "port" | "head" | "rtsp_port",
  label: string,
  limit: number,
  placeholder?: string,
  disabled?: boolean,
) {
  return (
    <FormInput
      label={label}
      value={props.form[field]}
      placeholder={placeholder ?? hint(props, field)}
      disabled={disabled}
      onChange={(value) => {
        if (typable(value, limit)) props.set(field, value);
      }}
    />
  );
}

/** The labelled options of a choice setting. */
const optionsOf = (key: SettingKey) =>
  (rowOf(key)?.kind.options ?? []) as readonly (readonly [string, string])[];

/** The name of the option `value` in choice setting `key`. */
const nameOf = (key: SettingKey, value: string) =>
  optionsOf(key).find(([option]) => option === value)?.[1] ?? value;

/** The name of an overlay mode. */
export const modeName = (mode: OverlayMode) => nameOf("camera.overlay", mode);

/** What the resolution list says in place of sizes. */
function resolutionStatus(
  asked: Asked<LiveResolutions>,
  hostUsable: boolean,
  sizes: readonly string[],
) {
  const { phase } = asked.state;
  if (!hostUsable) return "Enter a device host to list resolutions";
  if (phase === "loading") return "Loading resolutions…";
  if (phase === "failed") return "Could not read resolutions";
  return phase === "ready" && sizes.length === 0
    ? "Camera lists no resolutions"
    : "Resolutions not read yet";
}

/** The camera and its stream, down to the stream address of its own. */
export function ConnectionRows({
  resolutions,
  sizes,
  hostUsable,
  ...props
}: PanelProps & {
  resolutions: Asked<LiveResolutions>;
  sizes: readonly string[];
  hostUsable: boolean;
}) {
  const { form, set, settings } = props;
  const status = resolutionStatus(resolutions, hostUsable, sizes);
  // The web API carries the stream, so the RTSP port is not used.
  const viaWebApi = (form.rtsp_route || textOf(settings["camera.rtsp_route"])) === "web_api";
  return (
    <Form>
      {input(props, "label", "Name", {
        placeholder: nameHint(props),
        selectOnOpen: props.hidden !== null,
      })}
      {input(props, "host", "Host", { placeholder: hint(props, "host") ?? "e.g. 192.168.1.100" })}
      {input(props, "username", "Username")}
      {input(props, "password", "Password", { masked: true })}
      <FormSelect
        label="Protocol"
        value={form.protocol}
        options={[
          ["", `Default (${nameOf("camera.protocol", textOf(settings["camera.protocol"]))})`],
          ...(optionsOf("camera.protocol") as readonly (readonly [LiveForm["protocol"], string])[]),
        ]}
        onChange={(value) => set("protocol", value)}
      />
      {number(props, "port", "Port", 65535, "Standard")}
      {number(props, "head", "Camera Head", 9999)}
      <FormSelect
        label="RTSP Route"
        value={form.rtsp_route}
        options={[
          ["", `Default (${nameOf("camera.rtsp_route", textOf(settings["camera.rtsp_route"]))})`],
          ...(optionsOf("camera.rtsp_route") as readonly (readonly [
            LiveForm["rtsp_route"],
            string,
          ])[]),
        ]}
        onChange={(value) => set("rtsp_route", value)}
      />
      {number(props, "rtsp_port", "RTSP Port", 65535, undefined, viaWebApi)}
      <FormCombo
        label="Resolution"
        value={form.resolution}
        placeholder="Camera default"
        options={[
          { value: "", label: "Camera default" },
          ...(sizes.length
            ? sizes.map((size) => ({ value: size, label: size }))
            : [{ value: status, label: status, disabled: true }]),
        ]}
        onChange={(value) => set("resolution", value)}
        onOpen={() => {
          // An answer with no parameters, or none at all, is read again when the list is opened; one the camera gave
          // for the connection is kept, even when the head in it lists no sizes.
          const { state } = resolutions;
          const kept = state.phase === "ready" && state.value.listed;
          if (!kept && ["idle", "failed", "ready"].includes(state.phase)) resolutions.reload();
        }}
      />
      {input(props, "url", "Custom Stream URL", {
        placeholder: "Optional — overrides auto-built URL",
      })}
    </Form>
  );
}

/** What the camera's list of `what` says in place of choices: the line the list opens on. */
function choiceStatus(listing: Listing, what: string, hostUsable: boolean) {
  const { asked, fresh, choices } = listing;
  const { phase } = asked.state;
  if (!fresh) return { text: `Refresh to load ${what}`, failed: false };
  if (!hostUsable) return { text: `Enter a device host to load ${what}`, failed: false };
  switch (phase) {
    case "failed":
    case "invalid":
      return { text: `Could not load ${what}`, failed: true };
    case "ready":
      return { text: choices.length ? `Select ${what}` : `No ${what} available`, failed: false };
    default:
      return { text: `Loading ${what}…`, failed: false };
  }
}

/** The overlay modes whose camera lists data sources or topics to choose from. */
export type Listed = "mqtt" | "datahub";

/** What the camera lists for one mode, which stays while the connection does. */
export interface Listing {
  asked: Asked<unknown>;
  /** Whether the list was asked for under the connection as it stands now. */
  fresh: boolean;
  choices: readonly string[];
}

/**
 * One of the camera's own lists to choose from (data sources, topics), with a button to read it again. The form's
 * value shows only while the list has it, which is also what is added; a list that lacks it shows none.
 */
function Choice({
  props,
  field,
  label,
  listing,
  what,
  hostUsable,
  onRefresh,
}: {
  props: PanelProps;
  field: "source_key" | "topic";
  label: string;
  listing: Listing;
  /** The plural of what is listed, as the status lines say it. */
  what: string;
  hostUsable: boolean;
  onRefresh: () => void;
}) {
  const { choices, asked } = listing;
  const status = choiceStatus(listing, what, hostUsable);
  const value = chosen(props.form[field], choices);
  const { state } = asked;
  return (
    <FormSelect
      label={label}
      value={value}
      disabled={state.phase === "loading"}
      failed={status.failed}
      title={state.phase === "failed" ? state.text : undefined}
      options={[["", status.text], ...choices.map((choice) => [choice, choice] as const)]}
      onChange={(choice) => props.set(field, choice)}
      extra={
        <Button className="shrink-0" disabled={state.phase === "loading"} onClick={onRefresh}>
          Refresh
        </Button>
      }
    />
  );
}

/** The overlay: where it comes from, how it is read, and the page of what its transport needs. */
export function OverlayGroup({
  handlers,
  lists,
  hostUsable,
  onMode,
  onRefresh,
  ...props
}: PanelProps & {
  handlers: HandlersInfo | null;
  lists: Record<Listed, Listing>;
  hostUsable: boolean;
  onMode: (mode: OverlayMode) => void;
  onRefresh: (mode: Listed) => void;
}) {
  const { form, set } = props;
  const mode = form.overlay;
  // Only a decoder of streaming, timestamped records can decode a live stream.
  const decoders = (handlers?.decoders ?? []).filter((decoder) => decoder.live);
  const modes = optionsOf("camera.overlay") as readonly (readonly [OverlayMode, string])[];
  // A saved handler that is no longer installed stays visible instead of a blank select.
  const missing =
    handlers !== null &&
    form.handler !== "" &&
    !decoders.some((decoder) => decoder.handler === form.handler);
  const choice = (listed: Listed, field: "source_key" | "topic", label: string, what: string) => (
    <Choice
      props={props}
      field={field}
      label={label}
      what={what}
      listing={lists[listed]}
      hostUsable={hostUsable}
      onRefresh={() => onRefresh(listed)}
    />
  );
  const pages: Record<OverlayMode, ReactNode> = {
    none: (
      <>
        <FormNote>{OVERLAY_TEXT.none}</FormNote>
        {modes
          .filter(([other]) => other !== "none")
          .map(([other, name]) => (
            <FormNote key={other}>{`${name}: ${OVERLAY_TEXT[other]}`}</FormNote>
          ))}
      </>
    ),
    rtsp: <FormNote>{OVERLAY_TEXT.rtsp}</FormNote>,
    mqtt: (
      <>
        <FormNote>{OVERLAY_TEXT.mqtt}</FormNote>
        {input(props, "broker_host", "MQTT Host")}
        {input(props, "broker_user", "MQTT Username")}
        {input(props, "broker_pass", "MQTT Password", { masked: true })}
        {input(props, "broker_port", "MQTT Port")}
        {choice("mqtt", "source_key", "Data Source", "data sources")}
      </>
    ),
    datahub: (
      <>
        <FormNote>{OVERLAY_TEXT.datahub}</FormNote>
        {choice("datahub", "topic", "DataHub Topic", "DataHub topics")}
        {input(props, "channel", "Topic Channel", {
          title: "Instance of the DataHub topic to subscribe to. Most devices use 1.",
        })}
      </>
    ),
  };
  return (
    <fieldset className="m-0 min-w-0 rounded-control border border-line p-l pt-s">
      <legend className="px-xs font-semibold">Overlay</legend>
      <Form>
        <FormSelect label="Overlay Mode" value={mode} options={modes} onChange={onMode} />
        <FormSelect
          label="Handler Type"
          value={form.handler}
          disabled={mode === "none"}
          options={[
            ["", "(None)"],
            ...decoders.map((decoder) => [decoder.handler, decoder.display_name] as const),
            ...(missing ? [[form.handler, `${form.handler} (unavailable)`] as const] : []),
          ]}
          onChange={(value) => set("handler", value)}
        />
        <FormStack current={mode} pages={pages} />
      </Form>
    </fieldset>
  );
}
