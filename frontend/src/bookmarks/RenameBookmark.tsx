import { useEffect, useRef, useState } from "react";
import { Dialog } from "../kit/Dialog";
import { TextField } from "../kit/Fields";

/**
 * Asks for a bookmark's new text, starting with its current label selected. Empty text, or the label it has without
 * any text (`fallback`), takes the text away.
 */
export function RenameBookmark({
  label,
  fallback,
  onRename,
  onClose,
}: {
  label: string;
  fallback: string;
  onRename: (note: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(label);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.querySelector("input")?.select(), []);
  return (
    <Dialog
      title="Rename"
      onOk={() => {
        const trimmed = text.trim();
        onRename(trimmed === fallback ? "" : trimmed);
        onClose();
      }}
      onCancel={onClose}
    >
      <div ref={box}>
        <TextField label="Name" value={text} onChange={setText} />
      </div>
    </Dialog>
  );
}
