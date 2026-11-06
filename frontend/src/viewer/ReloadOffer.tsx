import { useState } from "react";
import { Button } from "../kit/Button";
import { useCan, useCommand } from "../session/session";
import { Banner } from "../shell/Banner";

/**
 * Offers to read an open item's files again after they changed on disk. `changed` counts the changes since the viewer
 * opened, so a dismissed offer comes back only with a later one; mount it per Content generation, as a reload starts a
 * new one. A seat that may not change the workspace is not offered.
 */
export function ReloadOffer({ item, changed }: { item: string; changed: number }) {
  const [dismissed, setDismissed] = useState(0);
  const command = useCommand();
  const allowed = useCan("content");
  if (!allowed || changed <= dismissed) return null;
  return (
    <Banner
      role="status"
      onDismiss={() => setDismissed(changed)}
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
