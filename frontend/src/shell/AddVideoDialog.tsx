import { useEffect, useState } from "react";
import type { DecoderInfo, HandlersInfo, ResolverInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select, TextField } from "../kit/Fields";
import { fetchHandlers } from "../resolvers/handlers";
import { needsValue, shown } from "../resolvers/fields";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import { useCommand } from "../session/session";
import { Refusal } from "./SettingField";

const NO_DECODERS: readonly DecoderInfo[] = [];

/**
 * The decoders that read a file named `path`, else all of them, so a file of an unknown kind can still be assigned.
 * A decoder reads a file whose lowercase name ends with one of its extensions, which may hold several dots
 * (`.acme.dat`): the rule of `Handlers::decoders_for`, which the intake applies when no decoder is named.
 */
export function decodersFor(
  decoders: readonly DecoderInfo[],
  path: string,
): readonly DecoderInfo[] {
  const name = (path.trim().split(/[\\/]/).pop() ?? "").toLowerCase();
  const reading = decoders.filter((decoder) =>
    decoder.extensions.some((extension) => name.endsWith(extension)),
  );
  return reading.length ? reading : decoders;
}

/** A decoder's options as the form a resolver's settings are, so the same field controls draw them. */
const optionsForm = (decoder?: DecoderInfo): ResolverInfo => ({
  id: decoder?.handler ?? "",
  display_name: decoder?.display_name ?? "",
  description: "",
  fields: decoder?.options ?? [],
});

/**
 * Asks for the path of a video on the machine that runs the backend, where this window has no file dialog of its own
 * to show: a remote server's window, or a browser, and optionally one overlay file with the options its decoder
 * declares. The backend's intake judges the paths and options; its refusal stays here.
 */
export function AddVideoDialog({ onClose }: { onClose: () => void }) {
  const command = useCommand();
  const [handlers, setHandlers] = useState<HandlersInfo | null>(null);
  const [path, setPath] = useState("");
  const [overlay, setOverlay] = useState("");
  const [picked, setPicked] = useState("");
  const [tried, setTried] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const request = new AbortController();
    // Without the handlers the overlay is sent for the backend to judge, with no options to ask for.
    fetchHandlers(request.signal).then(
      (answer) => {
        if (answer.ok) setHandlers(answer.value);
      },
      () => undefined, // aborted: the dialog closed
    );
    return () => request.abort();
  }, []);

  const candidates = overlay.trim() ? decodersFor(handlers?.decoders ?? NO_DECODERS, overlay) : [];
  const decoder = candidates.find((one) => one.handler === picked) ?? candidates[0];
  const form = useResolverForm(optionsForm(decoder), handlers?.decoders ?? NO_DECODERS);
  const missing = form.resolver.fields.find(
    (field) => needsValue(field) && !shown(field, form.values).trim(),
  );

  const ok = async () => {
    if (!path.trim()) {
      setFailure("Enter a path on the server.");
      return;
    }
    if (overlay.trim() && missing) {
      setTried(true);
      return;
    }
    setBusy(true);
    try {
      await command({
        type: "content",
        command: {
          type: "addItems",
          items: [
            {
              type: "Video",
              path: path.trim(),
              overlays: overlay.trim()
                ? [
                    {
                      path: overlay.trim(),
                      handler: decoder?.handler ?? null,
                      label: null,
                      options: form.settings ?? {},
                    },
                  ]
                : [],
            },
          ],
          open: true,
        },
      });
      onClose();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };
  return (
    <Dialog title="Add video" okLabel="Add" onOk={() => void ok()} onCancel={onClose} busy={busy}>
      <DialogBody>
        <TextField
          label="Path on the server"
          value={path}
          onChange={(text) => {
            setPath(text);
            setFailure(null);
          }}
        />
        {handlers && (
          <TextField
            label="Overlay path"
            value={overlay}
            onChange={(text) => {
              setOverlay(text);
              setFailure(null);
            }}
          />
        )}
        {candidates.length > 1 && (
          <Select
            label="Overlay format"
            value={decoder?.handler ?? ""}
            options={candidates.map((one) => [one.handler, one.display_name] as const)}
            onChange={setPicked}
          />
        )}
        {decoder && <ResolverForm form={form} />}
        {tried && overlay.trim() && missing && <Refusal reason={`${missing.label} is required`} />}
        {failure && (
          <p role="alert" className="m-0">
            {failure}
          </p>
        )}
      </DialogBody>
    </Dialog>
  );
}
