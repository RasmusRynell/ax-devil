import { command, type Shortcuts } from "../commands";
import type { CommandId } from "../generated/settings";
import { KeyChips } from "../kit/KeyChips";
import { RowList, type Row } from "../kit/RowList";
import { useSettings } from "../settings";

const GROUPS: readonly { heading: string; ids: readonly CommandId[] }[] = [
  { heading: "Open", ids: ["open-workspace", "add-video", "add-live-stream", "add-playlist"] },
  { heading: "Configure", ids: ["keyboard-shortcuts", "settings"] },
];

const row = (id: CommandId, shortcuts: Shortcuts): Row => {
  const shortcut = shortcuts[id];
  return {
    id,
    label: command(id).label,
    trailing: shortcut && (
      <span className="hidden sm:contents">
        <KeyChips shortcut={shortcut} />
      </span>
    ),
  };
};

/** The central welcome area: the main commands with their keys. */
export function Welcome({ onRun }: { onRun: (id: string) => void }) {
  const { shortcuts } = useSettings();
  return (
    <div className="m-auto grid w-full max-w-full grid-cols-2 gap-m p-m sm:flex sm:w-welcome sm:flex-col sm:gap-xl sm:p-xl">
      {GROUPS.map(({ heading, ids }) => (
        <RowList
          key={heading}
          heading={heading}
          rows={ids.map((id) => row(id, shortcuts))}
          onSelect={onRun}
        />
      ))}
    </div>
  );
}
