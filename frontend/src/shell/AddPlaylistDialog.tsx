import { useEffect, useRef, useState } from "react";
import { reasonOf } from "../errors";
import type { DecoderInfo, ResolverInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select, TextField } from "../kit/Fields";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import { resolvePlaylists } from "../resolvers/resolve";
import { HandlersFailure, useHandlers } from "../resolvers/useHandlers";
import { useCommand, useSession } from "../session/session";
import { Refusal } from "./SettingField";

/** Stands for no resolver while none is chosen. */
const NONE: ResolverInfo = { id: "", display_name: "", description: "", fields: [] };
const NO_RESOLVERS: readonly ResolverInfo[] = [];
const NO_DECODERS: readonly DecoderInfo[] = [];

/**
 * The Add Playlist dialog: the backend's handlers decide the resolvers and the form each takes. Until a source is
 * chosen, the sources are listed instead of a form. Adding resolves the playlists first and adds the item only when
 * that succeeds, so the dialog stays open meanwhile with the settings locked and Name and Cancel live. Cancel,
 * Escape and closing abort the resolve, and a refusal stays here until the settings change.
 */
export function AddPlaylistDialog({ onClose }: { onClose: () => void }) {
  const { handlers, failure, retry } = useHandlers();
  const resolvers = handlers?.resolvers ?? NO_RESOLVERS;
  const command = useCommand();
  const session = useSession().session?.id;
  const [resolverId, setResolverId] = useState("");
  const resolver = resolvers.find((candidate) => candidate.id === resolverId) ?? NONE;
  const form = useResolverForm(resolver, handlers?.decoders ?? NO_DECODERS);
  const [name, setName] = useState("");
  // The name at the moment the resolve succeeds, which may have been edited since it started.
  const latestName = useRef("");
  const [resolving, setResolving] = useState(false);
  const [refused, setRefused] = useState<{ reason: string; stamp: string } | null>(null);
  const request = useRef<AbortController | null>(null);

  // A refusal is about the settings it answered; any change since makes it stale.
  const stamp = JSON.stringify([resolver.id, form.settings]);
  const refusal = refused?.stamp === stamp ? refused.reason : null;

  useEffect(() => () => request.current?.abort(), []);
  const cancel = () => {
    request.current?.abort();
    onClose();
  };

  const add = async () => {
    if (resolving || !resolver.id || !form.settings || !session) return;
    if (form.unchosen) {
      setRefused({ reason: form.unchosen, stamp });
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setResolving(true);
    setRefused(null);
    try {
      const resolved = await resolvePlaylists(
        resolver.id,
        form.settings,
        session,
        controller.signal,
      );
      // A cancelled resolve adds nothing, even when its answer was already on its way.
      if (controller.signal.aborted) return;
      if (!resolved.ok) {
        setRefused({ reason: reasonOf(resolved.refusal), stamp });
        setResolving(false);
        return;
      }
      await command({
        type: "content",
        command: {
          type: "addResolved",
          item: resolved.value.item,
          label: latestName.current.trim() || null,
        },
      });
      onClose();
    } catch (error) {
      if (controller.signal.aborted) return;
      setRefused({ reason: error instanceof Error ? error.message : String(error), stamp });
      setResolving(false);
    }
  };
  return (
    <Dialog
      title="Add Playlist"
      okLabel="Add"
      onOk={() => void add()}
      onCancel={cancel}
      busy={resolving || !resolver.id || !form.settings}
    >
      {handlers ? (
        <DialogBody>
          <TextField
            label="Name"
            value={name}
            placeholder="Optional"
            onChange={(value) => {
              latestName.current = value;
              setName(value);
            }}
          />
          <fieldset disabled={resolving} className="contents">
            <Select
              label="Playlist source"
              value={resolver.id}
              options={[
                ["", "Select a source"],
                ...resolvers.map((candidate) => [candidate.id, candidate.display_name] as const),
              ]}
              onChange={setResolverId}
            />
            {resolver.id ? (
              <>
                {resolver.description && <p className="m-0 text-muted">{resolver.description}</p>}
                <ResolverForm form={form} />
              </>
            ) : (
              resolvers.map((candidate) => (
                <p key={candidate.id} className="m-0">
                  {candidate.description
                    ? `${candidate.display_name}: ${candidate.description}`
                    : candidate.display_name}
                </p>
              ))
            )}
          </fieldset>
        </DialogBody>
      ) : failure ? (
        <HandlersFailure failure={failure} retry={retry} />
      ) : (
        <p role="status" className="m-0 text-muted">
          Loading…
        </p>
      )}
      {resolving && (
        <p role="status" className="m-0 text-muted">
          Finding playlists…
        </p>
      )}
      {refusal && <Refusal reason={refusal} />}
    </Dialog>
  );
}
