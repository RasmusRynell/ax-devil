import { ask } from "../client/ask";
import { reasonOf } from "../client/errors";
import { ChoiceDialog } from "../kit/ChoiceDialog";

interface Props {
  onClose: () => void;
  /** Everything was removed; `summary` says how much. */
  onCleared: (summary: string) => void;
  /** The request failed, or some of the cache could not be removed; `message` says why. */
  onFailed: (message: string) => void;
}

/** Asks before removing the cache, then clears it and reports through `onCleared` or `onFailed`. */
export function ClearCacheDialog({ onClose, onCleared, onFailed }: Props) {
  const clear = async () => {
    onClose();
    const answer = await ask("POST /api/cache/clear");
    if (!answer.ok) return onFailed(reasonOf(answer.error));
    const { summary, failures } = answer.value;
    if (failures.length === 0) onCleared(summary);
    else onFailed([summary, ...failures].join("\n"));
  };
  return (
    <ChoiceDialog
      title="Clear Cache"
      message="Remove all cached frame indexes, overlay scenes and thumbnails?"
      choices={[
        { id: "clear", label: "Clear" },
        { id: "cancel", label: "Cancel" },
      ]}
      defaultId="cancel"
      onChoose={(choice) => (choice === "clear" ? void clear() : onClose())}
    />
  );
}
