import { useEffect, useId, useRef, type FormEvent, type ReactNode } from "react";
import { Button } from "./Button";

interface Props {
  title: string;
  children: ReactNode;
  onOk: () => void;
  onCancel: () => void;
  /** While true, OK is disabled, so the action cannot run twice. */
  busy?: boolean;
}

/** A modal dialog with OK and Cancel. Enter runs OK and Escape cancels; the browser traps focus inside. */
export function Dialog({ title, children, onOk, onCancel, busy = false }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy) onOk();
  };
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      className="m-auto max-h-full max-w-full overflow-auto rounded-popup border border-line-strong bg-popup p-0 text-fg backdrop:bg-scrim"
    >
      <form onSubmit={submit} className="flex flex-col gap-l p-xl">
        <h2 id={titleId} className="m-0 text-heading font-semibold">
          {title}
        </h2>
        {children}
        <div className="flex justify-end gap-m">
          <Button onClick={onCancel}>Cancel</Button>
          <Button type="submit" disabled={busy}>
            OK
          </Button>
        </div>
      </form>
    </dialog>
  );
}
