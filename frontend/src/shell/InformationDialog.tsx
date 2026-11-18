import { useState } from "react";
import { Dialog } from "../kit/Dialog";
import { Icon } from "../kit/Icon";
import { MiddleElided } from "../kit/MiddleElided";
import type { Information } from "../live/information";

/** One titled list of values, which its title folds away with the lists under it; those are indented. */
function Block({ info, level }: { info: Information; level: number }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <tr>
        <th
          colSpan={2}
          scope="colgroup"
          style={{ paddingLeft: `calc(var(--spacing-m) * ${level * 2})` }}
          className="px-m py-xs text-left font-semibold wrap-anywhere"
        >
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="flex w-full items-center gap-s bg-transparent text-left font-semibold text-fg focus-ring"
          >
            <Icon name={open ? "chevron-down" : "chevron-right"} />
            <span className="min-w-0 wrap-anywhere">{info.title}</span>
          </button>
        </th>
      </tr>
      {open &&
        info.fields.map(([label, value]) => (
          <tr key={label}>
            <th
              scope="row"
              style={{ paddingLeft: `calc(var(--spacing-m) * ${level * 2 + 2})` }}
              className="px-m py-xs text-left align-top font-normal text-muted"
            >
              <MiddleElided text={label} className="max-w-[24ch]" />
            </th>
            <td className="px-m py-xs select-text">
              <div
                role="textbox"
                aria-label={label}
                aria-readonly="true"
                tabIndex={0}
                className="max-w-[80ch] wrap-anywhere focus-ring"
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
                    event.preventDefault();
                    event.stopPropagation();
                    const range = document.createRange();
                    range.selectNodeContents(event.currentTarget);
                    const selection = window.getSelection();
                    selection?.removeAllRanges();
                    selection?.addRange(range);
                  }
                }}
              >
                {value}
              </div>
            </td>
          </tr>
        ))}
      {open &&
        info.children.map((child) => <Block key={child.title} info={child} level={level + 1} />)}
    </>
  );
}

/**
 * The information of a workspace item: its settings as a list of labelled values, with the lists of its lanes under it.
 * Values wrap inside the available width; the table scrolls and its corner lets the user resize it.
 */
export function InformationDialog({ info, onClose }: { info: Information; onClose: () => void }) {
  return (
    <Dialog title={info.title} onOk={onClose} okLabel="Close" focusOk>
      <div className="max-h-[80vh] max-w-full min-h-0 min-w-[min(40ch,100%)] resize overflow-auto">
        <table className="w-full border-collapse text-body [&_td>div]:wrap-anywhere">
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="px-m py-xs font-normal">
                Item
              </th>
              <th scope="col" className="px-m py-xs font-normal">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            <Block info={info} level={0} />
          </tbody>
        </table>
      </div>
    </Dialog>
  );
}
