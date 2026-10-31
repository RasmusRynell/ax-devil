import { keyParts } from "./shortcut";

/** A shortcut drawn as one chip per key. */
export function KeyChips({ shortcut }: { shortcut: string }) {
  return (
    <span className="flex shrink-0 gap-xs">
      {keyParts(shortcut).map((key) => (
        <kbd
          key={key}
          className="rounded-control border border-line-strong px-s font-sans text-small text-muted"
        >
          {key}
        </kbd>
      ))}
    </span>
  );
}
