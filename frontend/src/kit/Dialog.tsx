import { useEffect, useId, useRef, type FormEvent, type ReactNode } from "react";
import { Button } from "./Button";

interface Props {
  title: string;
  children: ReactNode;
  onOk: () => void;
  /** Without it the dialog has only OK, and Escape runs OK. */
  onCancel?: () => void;
  okLabel?: string;
  /** Opens with OK focused, for a dialog with nothing to fill in, so Enter confirms. */
  focusOk?: boolean;
  /** Extra buttons before OK, for a choice that Escape must not make. */
  footer?: ReactNode;
  /** While true, OK is disabled, so the action cannot run twice. */
  busy?: boolean;
}

/** A modal dialog with OK and Cancel. Enter runs OK and Escape cancels; the browser traps focus inside. */
export function Dialog({
  title,
  children,
  onOk,
  onCancel,
  okLabel = "OK",
  footer,
  focusOk = false,
  busy = false,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    if (focusOk) element?.querySelector<HTMLElement>("[type=submit]")?.focus();
  }, [focusOk]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy) onOk();
  };
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      data-cursor-region="popup"
      onCancel={(event) => {
        event.preventDefault();
        if (onCancel) onCancel();
        else if (!busy) onOk();
      }}
      className="m-auto max-h-full max-w-full overflow-auto rounded-popup border border-line-strong bg-popup p-0 text-fg backdrop:bg-scrim"
    >
      <form
        onSubmit={submit}
        className="flex max-h-[calc(100vh-var(--spacing-s))] flex-col gap-l p-xl [@media(max-height:20rem)]:gap-s [@media(max-height:20rem)]:p-m"
      >
        <h2 id={titleId} className="m-0 text-heading font-semibold">
          {title}
        </h2>
        {children}
        <div className="flex justify-end gap-m">
          {onCancel && <Button onClick={onCancel}>Cancel</Button>}
          {footer}
          <Button type="submit" disabled={busy}>
            {okLabel}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
