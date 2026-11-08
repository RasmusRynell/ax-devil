import type { ReactNode } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { QUIET } from "./look";
import { MiddleElided } from "./MiddleElided";
import { SectionCaption } from "./SectionCaption";

export interface Row {
  id: string;
  label: string;
  /** Muted under the label; it loses its middle before it wraps. */
  subtitle?: string;
  /** Drawn right-aligned, such as key chips. */
  trailing?: ReactNode;
  icon?: IconName;
  disabled?: boolean;
  /** Shown on hover. */
  title?: string;
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
      <SectionCaption heading={heading} plain />
      <ul className="m-0 flex list-none flex-col p-0">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              data-cursor-id={`row:${row.id}`}
              disabled={row.disabled}
              title={row.title}
              onClick={() => onSelect(row.id)}
              className={`${QUIET} w-full py-xs text-left`}
            >
              {row.icon && <Icon name={row.icon} />}
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">{row.label}</span>
                {row.subtitle && (
                  <MiddleElided text={row.subtitle} className="text-small text-muted" />
                )}
              </span>
              {row.trailing}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
