import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { reasonOf } from "../client/errors";
import type { HandlersInfo, ResolverInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Form, FormInput, FormSelect, FormStack } from "../kit/Form";
import { Refusal } from "../kit/Refusal";
import { ResolverForm, draftForm, sameSettings, useResolverForm } from "../resolvers/ResolverForm";
import { resolvePlaylists } from "../resolvers/resolve";
import type { Settings } from "../resolvers/scan";
import { HandlersFailure, useHandlers } from "../resolvers/useHandlers";
import { useCommand, useSession } from "../session/session";

/** Stands for no resolver while none is chosen. */
const NONE: ResolverInfo = { id: "", display_name: "", description: "", fields: [] };

/** What the form starts with: a source, its settings and the playlist's name, as an item holds them. */
export interface PlaylistDraft {
  resolver: string;
  label?: string | null;
  settings?: Settings;
}

/**
 * The Add Playlist dialog: the backend's handlers decide the resolvers and the form each takes. `initial` fills the
 * form in (a source that is not there leaves it on none), read once. Add resolves the playlists first and
 * adds the item only when that succeeds, so the dialog stays open meanwhile with the settings locked and Name and
 * Cancel live. A form with a count-only scan that has not answered scans first and goes on only if it found something
 * and the settings are the same when it answers. Cancel, Escape and closing abort the resolve, and a refusal stays here until the
 * settings change. With `editing`, the id of a playlist item, the source is fixed and the resolved playlists replace
 * that item, which keeps its id and place.
 */
export function AddPlaylistDialog({
  initial,
  editing,
  onClose,
}: {
  initial?: PlaylistDraft;
  editing?: string;
  onClose: () => void;
}) {
  const { handlers, failure, retry } = useHandlers();
  // The resolve in flight; null once its result is being added, which Cancel can no longer undo.
  const request = useRef<AbortController | null>(null);
  const closed = useRef(false);
  useEffect(() => () => request.current?.abort(), []);
  const close = () => {
    if (closed.current) return;
    closed.current = true;
    onClose();
  };
  const cancel = () => {
    request.current?.abort();
    close();
  };
  // The form starts from the saved settings, so it is built once the sources are known.
  return handlers ? (
    <PlaylistDialog {...{ handlers, initial, editing, request, closed, close, cancel }} />
  ) : (
    <Dialog title={editing ? "Edit Playlist" : "Add Playlist"} onCancel={cancel}>
      {failure ? (
        <HandlersFailure failure={failure} retry={retry} />
      ) : (
        <p role="status" className="m-0 text-muted">
          Loading…
        </p>
      )}
    </Dialog>
  );
}

function PlaylistDialog({
  handlers,
  initial,
  editing,
  request,
  closed,
  close,
  cancel,
}: {
  handlers: HandlersInfo;
  initial?: PlaylistDraft;
  editing?: string;
  request: RefObject<AbortController | null>;
  closed: RefObject<boolean>;
  close: () => void;
  cancel: () => void;
}) {
  const resolvers = handlers.resolvers;
  const command = useCommand();
  const session = useSession().session?.id;
  const [resolverId, setResolverId] = useState(initial?.resolver ?? "");
  const resolver = resolvers.find((candidate) => candidate.id === resolverId) ?? NONE;
  const form = useResolverForm(resolver, handlers.decoders, initial?.settings);
  const [name, setName] = useState(initial?.label ?? "");
  // The name at the moment the resolve succeeds, which may have been edited since it started.
  const latestName = useRef(name);
  // An edited playlist's source that this installation does not have, which nothing can restore.
  const lost = editing !== undefined && resolver.id === "" ? initial?.resolver : undefined;
  const [resolving, setResolving] = useState(false);
  const [refused, setRefused] = useState<{ reason: string; stamp: string } | null>(null);

  // A refusal is about the settings it answered; any change since makes it stale.
  const stamp = JSON.stringify([resolver.id, form.settings]);
  const refusal = refused?.stamp === stamp ? refused.reason : null;

  // Adding needs a source, its settings, a session to add to and no resolve already running; Add is disabled
  // otherwise.
  const settings = form.settings;
  const canAdd =
    !resolving && resolver.id !== "" && settings !== null && session !== undefined && !lost;

  const add = async () => {
    if (!canAdd) return;
    if (form.unchosen) {
      setRefused({ reason: form.unchosen, stamp });
      return;
    }
    const controller = new AbortController();
    // A load still counting pairs for another source is superseded.
    request.current?.abort();
    request.current = controller;
    setRefused(null);
    try {
      const loadable = form.settingsAfterScan ? await form.settingsAfterScan() : settings;
      if (!loadable || controller.signal.aborted) {
        if (request.current === controller) request.current = null;
        return;
      }
      // The same settings are the same playlists: a new name alone, or nothing, is all there is to save.
      if (editing && initial?.settings && sameSettings(loadable, initial.settings)) {
        const label = latestName.current.trim();
        if (label !== (initial.label ?? "")) {
          await command({
            type: "content",
            command: { type: "renameItem", item: editing, label },
          });
        }
        request.current = null;
        close();
        return;
      }
      setResolving(true);
      const resolved = await resolvePlaylists(resolver.id, loadable, session, controller.signal);
      // A cancelled resolve adds nothing, even when its answer was already on its way.
      if (controller.signal.aborted) return;
      if (!resolved.ok) {
        setRefused({ reason: reasonOf(resolved.error), stamp });
        setResolving(false);
        return;
      }
      // Sent, the add is committed: Cancel now only closes.
      request.current = null;
      const label = latestName.current.trim() || null;
      const ticket = resolved.value.item;
      await command({
        type: "content",
        command: editing
          ? { type: "editResolved", item: editing, ticket, label }
          : { type: "addResolved", item: ticket, label },
      });
      close();
    } catch (error) {
      if (controller.signal.aborted || closed.current) return;
      setRefused({ reason: error instanceof Error ? error.message : String(error), stamp });
      setResolving(false);
    }
  };
  // The page in view, with the other pages drawn as the person left them, out of sight, so the dialog is as big as the
  // biggest page and choosing a source does not resize it.
  const pages: Record<string, ReactNode> = {
    "": lost && (
      <div className="col-span-full">
        <Refusal reason={`Playlist source '${lost}' is unavailable.`} />
      </div>
    ),
    ...Object.fromEntries(
      resolvers.map((candidate) => [
        candidate.id,
        <ResolverForm
          key={candidate.id}
          form={candidate.id === resolver.id ? form : draftForm(form, candidate)}
        />,
      ]),
    ),
  };
  return (
    <Dialog
      title={editing ? "Edit Playlist" : "Add Playlist"}
      okLabel={editing ? "Save" : "Add"}
      onOk={lost ? undefined : () => void add()}
      onCancel={cancel}
      busy={!canAdd}
      error={refusal}
    >
      <DialogBody>
        <div className="w-form max-w-full min-w-0">
          <Form>
            <FormInput
              label="Name"
              value={name}
              placeholder="Optional"
              selectOnOpen={editing !== undefined}
              onChange={(value) => {
                latestName.current = value;
                setName(value);
              }}
            />
            <fieldset disabled={resolving || !!lost} className="contents">
              <FormSelect
                label="Playlist source"
                value={resolver.id}
                disabled={editing !== undefined}
                options={[
                  ["", "Select a source"],
                  ...resolvers.map((candidate) => [candidate.id, candidate.display_name] as const),
                ]}
                onChange={setResolverId}
              />
              <FormStack current={resolver.id} pages={pages} />
            </fieldset>
          </Form>
        </div>
      </DialogBody>
      {resolving && (
        <p role="status" className="m-0 text-muted">
          Finding playlists…
        </p>
      )}
    </Dialog>
  );
}
