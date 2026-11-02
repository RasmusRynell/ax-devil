import { useEffect, useState } from "react";
import { reasonOf } from "../errors";
import type { ErrorWire, HandlersInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select, TextField } from "../kit/Fields";
import { fetchHandlers } from "../resolvers/handlers";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import { useCommand } from "../session/session";
import { Refusal } from "./SettingField";

/** The Add Playlist dialog: the backend's handlers decide the resolvers and the form each takes. */
export function AddPlaylistDialog({ onClose }: { onClose: () => void }) {
  const [handlers, setHandlers] = useState<HandlersInfo | null>(null);
  const [failure, setFailure] = useState<ErrorWire | null>(null);
  useEffect(() => {
    const request = new AbortController();
    fetchHandlers(request.signal).then(
      (answer) => {
        if (answer.ok) setHandlers(answer.value);
        else setFailure(answer.refusal);
      },
      () => undefined, // aborted: the dialog closed
    );
    return () => request.abort();
  }, []);
  if (handlers?.resolvers.length) return <Loaded handlers={handlers} onClose={onClose} />;
  return (
    <Dialog title="Add Playlist" okLabel="Add" onOk={onClose} onCancel={onClose} busy>
      {failure ? (
        <Refusal reason={reasonOf(failure)} />
      ) : handlers ? (
        <Refusal reason="There are no playlist resolvers" />
      ) : (
        <p role="status" className="m-0 text-muted">
          Loading…
        </p>
      )}
    </Dialog>
  );
}

/**
 * The form, once the handlers are known. Adding hands the playlist to the backend, which resolves it in the
 * background and shows a failure on the item's row; only the intake's refusal stays here, until the settings change.
 */
function Loaded({ handlers, onClose }: { handlers: HandlersInfo; onClose: () => void }) {
  const command = useCommand();
  const [resolverId, setResolverId] = useState(handlers.resolvers[0].id);
  const resolver =
    handlers.resolvers.find((candidate) => candidate.id === resolverId) ?? handlers.resolvers[0];
  const form = useResolverForm(resolver, handlers.decoders);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [refused, setRefused] = useState<{ reason: string; stamp: string } | null>(null);

  // A refusal is about the settings it answered; any change since makes it stale.
  const stamp = JSON.stringify([resolver.id, form.settings]);
  const refusal = refused?.stamp === stamp ? refused.reason : null;

  const add = async () => {
    if (!form.settings) return;
    setAdding(true);
    setRefused(null);
    try {
      await command({
        type: "content",
        command: {
          type: "addItems",
          items: [
            {
              type: "Playlist",
              resolver: resolver.id,
              settings: form.settings,
              label: name.trim() || null,
            },
          ],
          open: true,
        },
      });
      onClose();
    } catch (error) {
      setRefused({ reason: error instanceof Error ? error.message : String(error), stamp });
      setAdding(false);
    }
  };
  return (
    <Dialog
      title="Add Playlist"
      okLabel="Add"
      onOk={() => void add()}
      onCancel={onClose}
      busy={adding || !form.settings}
    >
      <fieldset disabled={adding} className="contents">
        <DialogBody>
          <Select
            label="Playlist type"
            value={resolver.id}
            options={handlers.resolvers.map(
              (candidate) => [candidate.id, candidate.display_name] as const,
            )}
            onChange={setResolverId}
          />
          <ResolverForm form={form} />
          <TextField label="Name" value={name} onChange={setName} />
        </DialogBody>
      </fieldset>
      {refusal && <Refusal reason={refusal} />}
    </Dialog>
  );
}
