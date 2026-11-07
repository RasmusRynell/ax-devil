import { useEffect, useMemo, useRef, useState } from "react";
import { CommandFailed, reasonOf } from "../errors";
import type { HandlersInfo, LiveConnection, NewLiveWire } from "../generated/api";
import { useGrants } from "../access";
import { Dialog, DialogBody } from "../kit/Dialog";
import { seedOf, type AddSeed } from "../live/cameras";
import { askResolutions, askSources, DEBOUNCE_MS, sizesOf, useDiscovery } from "../live/discovery";
import { useExpansions } from "../live/expansions";
import { fetchHandlers } from "../resolvers/handlers";
import { useCommand, useDoc } from "../session/session";
import { useSettings } from "../settings";
import {
  ConnectionRows,
  modeName,
  OverlayGroup,
  type Listed,
  type Listing,
  type PanelProps,
} from "./LiveFields";
import {
  chosen,
  configuredOverlay,
  connectionOf,
  formFrom,
  formValues,
  headOf,
  hiddenSecrets,
  savedChoices,
  isField,
  newLive,
  problemOf,
  type FieldKey,
  type LiveForm,
  type OverlayMode,
} from "./liveForm";
import { textOf } from "./SettingField";

/** A refusal of the last OK, about the form as it stood. */
interface Refused {
  reason: string;
  /** Whether the backend judged the form, so the same form is refused again; false for a failure to reach it. */
  judged: boolean;
  stamp: string;
}

/**
 * What the camera lists for `kind` under `connection`: asked for once `askedFor` is the connection as it stands, and
 * kept while it is, whichever mode the form is on. `kept` is a choice saved with an edited stream, which the list offers
 * whether the camera lists it or not.
 */
function useListing(
  kind: Listed,
  connection: LiveConnection,
  key: string,
  askedFor: string | null,
  hostKnown: boolean,
  kept: string,
): Listing {
  const fresh = askedFor === key;
  const asked = useDiscovery(
    fresh && hostKnown ? (signal) => askSources(connection, kind, signal) : null,
    key,
    0,
  );
  const { state } = asked;
  const listed =
    state.phase === "ready"
      ? state.value.overlay === "mqtt"
        ? state.value.sources
        : state.value.topics
      : [];
  const choices = kept && !listed.includes(kept) ? [kept, ...listed] : listed;
  return { asked, fresh, choices };
}

/**
 * Adds a live stream, in the layout of the Python app's dialog: one page of connection fields, an Overlay group
 * with the page of the chosen mode, one line saying what is wrong, and OK, which waits for a form with nothing wrong.
 * Blank fields take the configured defaults, so the form shows them as placeholders; the camera is asked for its stream
 * sizes, analytics sources and topics but never connected for the stream itself: the viewer does that. The backend's
 * intake judges the form, and its refusal is the line under the groups. With `editing`, the id of a stream, `initial`
 * is its recipe and OK replaces that stream, which keeps its id and place; the form takes no defaults, a blank
 * password is the saved one, and a saved data source or topic stays chosen until the connection changes.
 */
export function AddLiveStreamDialog({
  seed = {},
  initial,
  editing,
  title = editing ? "Edit Live Stream" : "Add Live Stream",
  okLabel = editing ? "Save" : "OK",
  onClose,
}: {
  /** What the form starts from, read once when the dialog opens. */
  seed?: AddSeed;
  /** A stream's recipe to start from instead of `seed`. */
  initial?: Partial<NewLiveWire>;
  editing?: string;
  title?: string;
  okLabel?: string;
  onClose: () => void;
}) {
  const command = useCommand();
  const settings = useSettings();
  const values = useMemo(() => formValues(settings), [settings]);
  const items = useDoc()?.workspace.items ?? [];
  const [start] = useState(() => initial ?? seedOf(seed, items));
  const like = editing ?? seed.like;
  const [form, setForm] = useState<LiveForm>(() => formFrom(values, start));
  const [handlers, setHandlers] = useState<HandlersInfo | null>(null);
  const [adding, setAdding] = useState(false);
  const [refused, setRefused] = useState<Refused | null>(null);
  // The connection the camera was last asked about for each mode's list, which is read on choosing the mode and on Refresh.
  // A changed connection forgets both, so coming back to an earlier one lists nothing until a mode choice or Refresh.
  const [asked, setAsked] = useState<Record<Listed, string | null>>({ mqtt: null, datahub: null });
  // Whether the person at the form has chosen the overlay or its decoder, which the configured ones no longer replace.
  const edited = useRef(false);

  const hostDefault = textOf(values["camera.host"]);
  const hostKnown = !!(form.host.trim() || hostDefault.trim());
  // Questions are asked as the stream will be added: `like` lets the backend use that stream's saved login.
  const connection: LiveConnection = { ...connectionOf(form, false, !!editing), like };
  const connectionKey = JSON.stringify(connection);
  // The camera lists every head's sizes in one answer, kept for the connection while the head is chosen from it.
  const resolutions = useDiscovery(
    hostKnown ? (signal) => askResolutions(connection, signal) : null,
    connectionKey,
    DEBOUNCE_MS,
  );
  // The connection the form opened with: the choices saved with an edited stream hold only while it stands.
  const [opened] = useState(connectionKey);
  const saved = connectionKey === opened ? savedChoices(start) : undefined;
  const current = useRef(connectionKey);
  useEffect(() => {
    if (current.current === connectionKey) return;
    current.current = connectionKey;
    setAsked({ mqtt: null, datahub: null });
  }, [connectionKey]);
  const mode = form.overlay;
  const lists = {
    mqtt: useListing("mqtt", connection, connectionKey, asked.mqtt, hostKnown, saved?.mqtt ?? ""),
    datahub: useListing(
      "datahub",
      connection,
      connectionKey,
      asked.datahub,
      hostKnown,
      saved?.datahub ?? "",
    ),
  };
  const sizes =
    resolutions.state.phase === "ready"
      ? sizesOf(resolutions.state.value, headOf(form, textOf(values["camera.head"])))
      : [];

  // Once the decoders are known, the form starts on the overlay the settings choose when its decoder can read a stream.
  const decoded = useRef(false);
  useEffect(() => {
    const request = new AbortController();
    fetchHandlers(request.signal).then(
      (answer) => {
        if (answer.ok) setHandlers(answer.value);
      },
      () => undefined, // aborted: the dialog closed
    );
    return () => request.abort();
  }, []);
  useEffect(() => {
    if (!handlers || decoded.current) return;
    decoded.current = true;
    const configured =
      start.overlay || edited.current ? undefined : configuredOverlay(values, handlers.decoders);
    if (!configured) return;
    setForm((now) => ({ ...now, ...configured }));
    if (configured.overlay === "mqtt" || configured.overlay === "datahub") {
      const { overlay } = configured;
      setAsked((now) => ({ ...now, [overlay]: current.current }));
    }
  }, [handlers, values, start]);

  const onMode = (next: OverlayMode) => {
    edited.current = true;
    setForm((now) => ({ ...now, overlay: next }));
    if (next !== "mqtt" && next !== "datahub") return;
    // A mode's list is read when the mode is chosen unless it has been, as long as the connection stands.
    const { asked: list, fresh } = lists[next];
    if (!fresh) setAsked((now) => ({ ...now, [next]: connectionKey }));
    else if (list.state.phase === "failed" || list.state.phase === "invalid") list.reload();
  };
  const onRefresh = (kind: Listed) => {
    const { asked: list, fresh } = lists[kind];
    if (fresh) list.reload();
    else setAsked((now) => ({ ...now, [kind]: connectionKey }));
  };

  // What is added: the chosen mode's data source or topic is one the camera lists; the other mode's is kept as it was.
  const sent: LiveForm = {
    ...form,
    ...(mode === "mqtt" && { source_key: chosen(form.source_key, lists.mqtt.choices) }),
    ...(mode === "datahub" && { topic: chosen(form.topic, lists.datahub.choices) }),
  };
  const stamp = JSON.stringify(sent);
  const refusal = refused?.stamp === stamp ? refused : null;
  const listed = mode === "mqtt" || mode === "datahub" ? lists[mode].asked.state : undefined;
  const invalid = [resolutions.state, listed].flatMap((state) =>
    state?.phase === "invalid" ? [state.error] : [],
  );
  // The backend expands `$NAME` references: one that names nothing set is refused outside any field, so no host yet.
  const hostUsable = hostKnown && invalid.every((error) => isField(error.location));

  // A host reference that names nothing is the first thing wrong, as a blank host is.
  const unset = invalid.find((error) => !isField(error.location));
  const wrong =
    (unset && reasonOf(unset)) ??
    problemOf(sent, values, modeName(mode)) ??
    (invalid[0] && reasonOf(invalid[0])) ??
    (refusal?.judged ? refusal.reason : undefined);
  const message = wrong ?? refusal?.reason ?? "";

  // Only the person at this machine may ask what a reference expands to.
  const expansions = useExpansions(
    form.host,
    form.broker_host,
    useGrants().includes("preferences"),
  );
  const props: PanelProps = {
    form,
    set: <K extends FieldKey>(field: K, value: LiveForm[K]) => {
      if (field === "handler") edited.current = true;
      setForm((now) => ({ ...now, [field]: value }));
    },
    settings: values,
    expansions,
    hidden: editing ? hiddenSecrets(start) : null,
  };

  const add = async () => {
    setAdding(true);
    setRefused(null);
    try {
      await command({
        type: "content",
        command: editing
          ? { type: "editItem", item: editing, new: { type: "Live", ...newLive(sent, true) } }
          : {
              type: "addItems",
              items: [{ type: "Live", ...newLive(sent), like }],
              open: true,
            },
      });
      onClose();
    } catch (error) {
      setRefused({
        reason: error instanceof Error ? error.message : String(error),
        judged: error instanceof CommandFailed,
        stamp,
      });
      setAdding(false);
    }
  };

  return (
    <Dialog
      title={title}
      okLabel={okLabel}
      onOk={() => void add()}
      onCancel={onClose}
      busy={adding || !!wrong}
    >
      <fieldset disabled={adding} className="contents">
        <DialogBody>
          <div className="flex w-form max-w-full min-w-0 flex-col gap-l">
            <ConnectionRows
              {...props}
              resolutions={resolutions}
              sizes={sizes}
              hostUsable={hostUsable}
            />
            <OverlayGroup
              {...props}
              handlers={handlers}
              lists={lists}
              hostUsable={hostUsable}
              onMode={onMode}
              onRefresh={onRefresh}
            />
            <p
              role={message ? "alert" : undefined}
              className="m-0 min-h-lh text-danger [overflow-wrap:anywhere]"
            >
              {message}
            </p>
          </div>
        </DialogBody>
      </fieldset>
    </Dialog>
  );
}
