import { reasonOf } from "../client/errors";
import type { ReloadFailure } from "../generated/api";
import { Button } from "../kit/Button";
import { useCan, useCommand } from "../session/session";
import { Banner } from "../kit/Banner";

/**
 * Offers to read an open item's files again after they changed on disk. `changed` counts the changes since the viewer
 * opened and `dismissed` the count the user dismissed, which the host keeps so switching tabs does not bring the offer
 * back: it returns only with a later change. A seat that may not change the workspace is not offered.
 */
export function ReloadOffer({
  item,
  changed,
  dismissed,
  onDismiss,
}: {
  item: string;
  changed: number;
  dismissed: number;
  onDismiss: (count: number) => void;
}) {
  const command = useCommand();
  const allowed = useCan("content");
  if (!allowed || changed <= dismissed) return null;
  return (
    <Banner
      role="status"
      onDismiss={() => onDismiss(changed)}
      actions={
        <Button
          size="small"
          // A refused reload leaves the offer up, which says so.
          onClick={() =>
            void command({ type: "content", command: { type: "resolve", item } }).catch(
              () => undefined,
            )
          }
        >
          Reload
        </Button>
      }
    >
      Source changed, reload current viewer?
    </Banner>
  );
}

/**
 * Says why the last reload of an open item failed. The viewer keeps its old content, so this is a warning above it
 * that the user can dismiss; `dismissed` is the attempt dismissed, so the next failure shows again.
 */
export function ReloadFailed({
  failure,
  dismissed,
  onDismiss,
}: {
  failure: ReloadFailure | undefined;
  dismissed: number;
  onDismiss: (attempt: number) => void;
}) {
  if (!failure || failure.attempt <= dismissed) return null;
  return (
    <Banner role="alert" onDismiss={() => onDismiss(failure.attempt)}>
      Could not reload: {reasonOf(failure.error)}
    </Banner>
  );
}
