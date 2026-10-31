import { command } from "../commands";
import { KeyChips } from "../kit/KeyChips";
import { RowList, type Row } from "../kit/RowList";

const GROUPS: readonly { heading: string; ids: readonly string[] }[] = [
  { heading: "Open", ids: ["open-workspace", "add-video", "add-live-stream", "add-playlist"] },
  { heading: "Configure", ids: ["keyboard-shortcuts", "settings"] },
];

const row = (id: string): Row => {
  const { label, shortcut } = command(id);
  return { id, label, trailing: shortcut && <KeyChips shortcut={shortcut} /> };
};

/** The central welcome area: the main commands with their keys. */
export function Welcome({ onRun }: { onRun: (id: string) => void }) {
  return (
    <div className="flex w-welcome max-w-full flex-col gap-xl m-auto p-xl">
      {GROUPS.map(({ heading, ids }) => (
        <RowList key={heading} heading={heading} rows={ids.map(row)} onSelect={onRun} />
      ))}
    </div>
  );
}
