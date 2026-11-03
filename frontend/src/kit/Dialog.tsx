import {
  useEffect,
  useId,
  useRef,
  type FormEvent,
  type ReactEventHandler,
  type ReactNode,
} from "react";
import { Button } from "./Button";

interface FrameProps {
  title: string;
  /** The body, between the title and the footer. */
  children: ReactNode;
  /** The row of answer buttons, which stays in view while a body that can scroll does. */
  footer: ReactNode;
  /** The browser's Escape request, which the dialog turns into its own answer. */
  onCancel: ReactEventHandler<HTMLDialogElement>;
  onSubmit?: (event: FormEvent) => void;
  /** Opens with the submit button focused, for a dialog with nothing to fill in, so Enter confirms. */
  focusSubmit?: boolean;
  /** The browser closed the dialog by itself. */
  onClose?: ReactEventHandler<HTMLDialogElement>;
}

/** The modal box shared by the kit's dialogs: opened on mount, titled, with a footer under the body. */
export function DialogFrame({
  title,
  children,
  footer,
  onCancel,
  onSubmit,
  focusSubmit = false,
  onClose,
}: FrameProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    if (focusSubmit) element?.querySelector<HTMLElement>("[type=submit]")?.focus();
  }, [focusSubmit]);
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      data-cursor-region="popup"
      onCancel={onCancel}
      onClose={onClose}
      className="m-auto max-h-full max-w-full overflow-auto rounded-popup border border-line-strong bg-popup p-0 text-fg backdrop:bg-scrim"
    >
      <form
        onSubmit={onSubmit}
        className="flex max-h-[calc(100vh-var(--spacing-s))] flex-col gap-l p-xl [@media(max-height:20rem)]:gap-s [@media(max-height:20rem)]:p-m"
      >
        <h2 id={titleId} className="m-0 text-heading font-semibold">
          {title}
        </h2>
        {children}
        {footer}
      </form>
    </dialog>
  );
}

interface Props {
  title: string;
  children: ReactNode;
  onOk: () => void;
  /** Without it the dialog has only OK, and Escape runs OK. */
  onCancel?: () => void;
  okLabel?: string;
  /** Opens with OK focused, for a dialog with nothing to fill in, so Enter confirms. */
  focusOk?: boolean;
  /** Extra buttons after OK, for a choice that Escape must not make. */
  footer?: ReactNode;
  /** A button at the left of the footer, apart from the answers, for an action on the dialog's own state. */
  footerStart?: ReactNode;
  /** While true, OK is disabled, so the action cannot run twice. */
  busy?: boolean;
}

/** A modal dialog with OK, then Cancel. Enter runs OK and Escape cancels; the browser traps focus inside. */
export function Dialog({
  title,
  children,
  onOk,
  onCancel,
  okLabel = "OK",
  footer,
  footerStart,
  focusOk = false,
  busy = false,
}: Props) {
  return (
    <DialogFrame
      title={title}
      focusSubmit={focusOk}
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy) onOk();
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (onCancel) onCancel();
        else if (!busy) onOk();
      }}
      footer={
        <div className="flex gap-m">
          {footerStart}
          <div className="ml-auto flex gap-m">
            <Button type="submit" disabled={busy}>
              {okLabel}
            </Button>
            {footer}
            {onCancel && <Button onClick={onCancel}>Cancel</Button>}
          </div>
        </div>
      }
    >
      {children}
    </DialogFrame>
  );
}

/** The fields of a dialog: they scroll when the window is too small, and the title and buttons stay in view. */
export function DialogBody({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-l overflow-y-auto [@media(max-height:20rem)]:gap-s">
      {children}
    </div>
  );
}
