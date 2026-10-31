import type { ReactNode } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";

export interface Row {
  id: string;
  label: string;
  /** Drawn right-aligned, such as key chips. */
  trailing?: ReactNode;
  icon?: IconName;
}

interface Props {
  /** The group name, drawn as a caption above the rows. */
  heading: string;
  rows: readonly Row[];
  onSelect: (id: string) => void;
}

/** A captioned group of full-width action rows with a right-aligned trailing part. */
export function RowList({ heading, rows, onSelect }: Props) {
  return (
    <section className="flex flex-col gap-xs">
      <h2 className="px-m text-small font-semibold text-muted uppercase">{heading}</h2>
      <ul className="m-0 flex list-none flex-col p-0">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onSelect(row.id)}
              className="flex min-h-row w-full items-center py-xs gap-m rounded-control bg-transparent px-m text-left text-fg hover:bg-hover focus-ring"
            >
              {row.icon && <Icon name={row.icon} />}
              <span className="min-w-0 flex-1">{row.label}</span>
              {row.trailing}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
