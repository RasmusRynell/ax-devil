import { useEffect, useRef, useState } from "react";
import type { ApiError, HandlersInfo } from "../generated/api";
import { Dialog } from "../kit/Dialog";
import { Select, TextField } from "../kit/Fields";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import { addPlaylist, fetchHandlers } from "../workspace/client";
import { Refusal } from "./SettingField";

/** The Add Playlist dialog: the backend's handlers decide the resolvers and the form each takes. */
export function AddPlaylistDialog({ onClose }: { onClose: () => void }) {
  const [handlers, setHandlers] = useState<HandlersInfo | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
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
    <Dialog title="Add Playlist" okLabel="Load" onOk={onClose} onCancel={onClose} busy>
      {failure ? (
        <Refusal reason={failure.reason} />
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

function Loaded({ handlers, onClose }: { handlers: HandlersInfo; onClose: () => void }) {
  const [resolverId, setResolverId] = useState(handlers.resolvers[0].id);
  const resolver =
    handlers.resolvers.find((candidate) => candidate.id === resolverId) ?? handlers.resolvers[0];
  const form = useResolverForm(resolver, handlers.decoders);
  const [name, setName] = useState("");
  const [resolving, setResolving] = useState(false);
  const [refused, setRefused] = useState<{ error: ApiError; stamp: string } | null>(null);
  const request = useRef<AbortController | null>(null);
  // Closing the dialog tells the backend to stop resolving.
  useEffect(() => () => request.current?.abort(), []);

  // A refusal is about the settings it answered; any change since makes it stale.
  const stamp = JSON.stringify([resolver.id, form.settings]);
  const refusal = refused?.stamp === stamp ? refused.error : null;

  const load = async () => {
    if (!form.settings) return;
    const controller = new AbortController();
    request.current = controller;
    setResolving(true);
    setRefused(null);
    const error = await addPlaylist(
      { resolver: resolver.id, settings: form.settings, label: name.trim() || null },
      controller.signal,
    ).catch(() => null); // aborted: the dialog closed
    if (controller.signal.aborted) return;
    setResolving(false);
    if (error) setRefused({ error, stamp });
    else onClose();
  };
  const aboutAField = resolver.fields.some((field) => field.key === refusal?.location);
  return (
    <Dialog
      title="Add Playlist"
      okLabel="Load"
      onOk={() => void load()}
      onCancel={onClose}
      busy={resolving || !form.settings}
    >
      <fieldset disabled={resolving} className="contents">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-l overflow-y-auto [@media(max-height:20rem)]:gap-s">
          <Select
            label="Playlist type"
            value={resolver.id}
            options={handlers.resolvers.map(
              (candidate) => [candidate.id, candidate.display_name] as const,
            )}
            onChange={setResolverId}
          />
          <ResolverForm form={form} refusal={refusal} />
          <TextField label="Name" value={name} onChange={setName} />
        </div>
      </fieldset>
      {refusal && !aboutAField && <Refusal reason={refusal.reason} />}
    </Dialog>
  );
}
