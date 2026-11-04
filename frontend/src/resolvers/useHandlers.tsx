import { useEffect, useState } from "react";
import { reasonOf } from "../errors";
import type { ErrorWire, HandlersInfo } from "../generated/api";
import { Button } from "../kit/Button";
import { Refusal } from "../shell/SettingField";
import { fetchHandlers } from "./handlers";

/** The backend's handlers, loaded when the dialog opens: the handlers, else why they could not be loaded. */
export function useHandlers(): {
  handlers: HandlersInfo | null;
  failure: ErrorWire | null;
  retry: () => void;
} {
  const [handlers, setHandlers] = useState<HandlersInfo | null>(null);
  const [failure, setFailure] = useState<ErrorWire | null>(null);
  const [attempt, setAttempt] = useState(0);
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
  }, [attempt]);
  return {
    handlers,
    failure,
    retry: () => {
      setFailure(null);
      setAttempt(attempt + 1);
    },
  };
}

/** Why the handlers could not be loaded, with Retry. */
export function HandlersFailure({ failure, retry }: { failure: ErrorWire; retry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-s">
      <Refusal reason={reasonOf(failure)} />
      <Button onClick={retry}>Retry</Button>
    </div>
  );
}
