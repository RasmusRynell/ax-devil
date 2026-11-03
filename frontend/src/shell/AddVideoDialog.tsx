import { useState } from "react";
import type { DecoderInfo, ResolverInfo } from "../generated/api";
import { Dialog, DialogBody } from "../kit/Dialog";
import { Select, TextField } from "../kit/Fields";
import { needsValue, shown } from "../resolvers/fields";
import { ResolverForm, useResolverForm } from "../resolvers/ResolverForm";
import { HandlersFailure, useHandlers } from "../resolvers/useHandlers";
import { useCommand } from "../session/session";
import { Refusal } from "./SettingField";

const NO_DECODERS: readonly DecoderInfo[] = [];

/** The name of the file at `path`, without its folders. */
const fileName = (path: string) => path.trim().split(/[\\/]/).pop() ?? "";

/**
 * The decoders that read a file named `path`, else all of them, so a file of an unknown kind can still be assigned.
 * A decoder reads a file whose lowercase name ends with one of its extensions, which may hold several dots
 * (`.acme.dat`): the rule of `Handlers::decoders_for`, which the intake applies when no decoder is named.
 */
export function decodersFor(
  decoders: readonly DecoderInfo[],
  path: string,
): readonly DecoderInfo[] {
  const name = fileName(path).toLowerCase();
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
  const { handlers, failure: unavailable, retry } = useHandlers();
  const [path, setPath] = useState("");
  const [overlay, setOverlay] = useState("");
  const [picked, setPicked] = useState("");
  const [tried, setTried] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const candidates = overlay.trim() ? decodersFor(handlers?.decoders ?? NO_DECODERS, overlay) : [];
  // Only a sole candidate is chosen for the user; with several, a pick stays only while it is still a candidate.
  const decoder =
    candidates.length === 1 ? candidates[0] : candidates.find((one) => one.handler === picked);
  const undecided = candidates.length > 1 && !decoder;
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
    <Dialog
      title="Add video"
      okLabel="Add"
      onOk={() => void ok()}
      onCancel={onClose}
      busy={busy || undecided}
    >
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
              // A pick that does not read this file is forgotten, so it cannot return for a later one.
              if (!decodersFor(handlers.decoders, text).some((one) => one.handler === picked)) {
                setPicked("");
              }
              setFailure(null);
            }}
          />
        )}
        {candidates.length > 1 && (
          <Select
            label="Data handler"
            value={decoder?.handler ?? ""}
            options={[
              ["", "(None)"],
              ...candidates.map((one) => [one.handler, one.display_name] as const),
            ]}
            onChange={setPicked}
          />
        )}
        {undecided && (
          <p role="status" className="m-0 text-muted">
            {`Choose the data handler that reads ${fileName(overlay)}.`}
          </p>
        )}
        {decoder && <ResolverForm form={form} />}
        {unavailable && <HandlersFailure failure={unavailable} retry={retry} />}
        {tried && overlay.trim() && missing && <Refusal reason={`${missing.label} is required`} />}
        {failure && <Refusal reason={failure} />}
      </DialogBody>
    </Dialog>
  );
}
