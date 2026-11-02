import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api";
import { Button } from "../kit/Button";
import { Dialog } from "../kit/Dialog";

/** An error nobody caught, as the dialog shows it. */
interface Uncaught {
  /** `Name: message`. */
  summary: string;
  /** The summary with the stack trace, as copied. */
  details: string;
}

/** Errors held for the dialog at once; the browser console still has the rest. */
const MAX_HELD = 10;

/** Puts `text` on the clipboard when the page may; otherwise the details stay selectable on screen. */
async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // An insecure origin has no clipboard. Failing here must not raise another error dialog.
  }
}

/** Tells the backend, which logs it with who sent it. A failure to tell is dropped: it must not raise another dialog. */
function report({ summary, details }: Uncaught): void {
  apiFetch("/api/client-errors", {
    method: "POST",
    headers: { "content-type": "application/json" },
    // The address without its query and fragment, which may hold a credential.
    body: JSON.stringify({
      summary,
      details,
      page: `${window.location.origin}${window.location.pathname}`,
      desktop: window.axDevilWindow !== undefined,
    }),
  }).catch(() => undefined);
}

function describe(thrown: unknown): Uncaught {
  if (thrown instanceof Error) {
    const summary = `${thrown.name}: ${thrown.message}`;
    return {
      summary,
      details: thrown.stack?.includes(summary)
        ? thrown.stack
        : `${summary}\n${thrown.stack ?? ""}`.trim(),
    };
  }
  let summary: string;
  try {
    summary = typeof thrown === "string" ? thrown : (JSON.stringify(thrown) ?? String(thrown));
  } catch {
    // A circular object or a BigInt: show what String makes of it.
    summary = String(thrown);
  }
  return { summary, details: summary };
}

/**
 * Shows an error that nothing caught (a script error or a rejected promise nobody handled) in a dialog, one at a time,
 * with its stack trace to read and copy. Continue goes on, though some features may fail; in the desktop app Close
 * closes the window and in a browser Reload reloads the page. It draws apart from the app, so an error that takes the
 * app down still shows. Each error is also sent to the backend's log, and the browser keeps it in its console.
 */
export function UncaughtErrors() {
  const [held, setHeld] = useState<Uncaught[]>([]);
  const count = useRef(0);
  useEffect(() => {
    const hold = (thrown: unknown) => {
      // An error that is dropped is not reported either, so one that repeats every frame cannot flood the log.
      if (count.current >= MAX_HELD) return;
      count.current++;
      const error = describe(thrown);
      report(error);
      setHeld((now) => [...now, error]);
    };
    // An error event without an error object is the browser's own notice, such as a ResizeObserver loop.
    const onError = (event: ErrorEvent) => event.error != null && hold(event.error);
    const onRejection = (event: PromiseRejectionEvent) => hold(event.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  const [shown] = held;
  if (!shown) return null;
  const bridge = window.axDevilWindow;
  return (
    <Dialog
      title="Application Error"
      okLabel="Continue"
      focusOk
      onOk={() => {
        count.current--;
        setHeld((now) => now.slice(1));
      }}
      footer={
        bridge ? (
          <Button onClick={() => bridge.close()}>Close</Button>
        ) : (
          <Button onClick={() => window.location.reload()}>Reload</Button>
        )
      }
    >
      <div className="flex max-w-[60ch] min-h-0 min-w-0 flex-col gap-m overflow-y-auto">
        <p className="m-0">An unexpected error occurred:</p>
        <p className="m-0 font-semibold [overflow-wrap:anywhere]">{shown.summary}</p>
        <p className="m-0 text-muted">ax-devil will try to continue, but some features may fail.</p>
        <details className="flex flex-col gap-m">
          <summary className="cursor-pointer">Details</summary>
          <pre className="m-0 max-h-[40vh] overflow-auto rounded-control border border-line p-m text-small whitespace-pre-wrap select-text [overflow-wrap:anywhere]">
            {shown.details}
          </pre>
          <Button icon="copy" className="self-start" onClick={() => void copy(shown.details)}>
            Copy
          </Button>
        </details>
      </div>
    </Dialog>
  );
}
