import { useId, type ReactNode } from "react";
import { EditableLabel } from "./EditableLabel";

interface Props {
  title: string;
  /** The title's tooltip when it should say more than the title; the title itself otherwise. */
  tooltip?: string;
  /** The `data-cursor-id` of the title, so a shared pointer finds it when the other window shows another view. */
  cursorId?: string;
  /** A control at the right end of the header, such as a refresh button. */
  action?: ReactNode;
  /** Makes the title editable in place: `label` names the field, `onEdit` hears each change, `onDone` the end. */
  edit?: { label: string; onEdit: (text: string) => void; onDone: () => void };
  children: ReactNode;
}

/**
 * A sidebar view: a header whose long title ends in an ellipsis and shows in full as its tooltip, over a body that
 * scrolls vertically (never sideways) and keeps short content at the top.
 */
export function ViewPanel({ title, tooltip, cursorId, action, edit, children }: Props) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-h-0 min-w-0 flex-1 flex-col bg-panel text-fg"
    >
      <header className="flex h-header shrink-0 items-center gap-m border-b border-line pl-l pr-m">
        <h2
          id={titleId}
          title={tooltip ?? title}
          data-cursor-id={cursorId}
          className="m-0 min-w-0 flex-1 truncate font-semibold"
        >
          {edit ? (
            <EditableLabel
              value={title}
              tooltip={tooltip}
              label={edit.label}
              onEdit={edit.onEdit}
              onDone={edit.onDone}
            />
          ) : (
            title
          )}
        </h2>
        {action}
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-auto">
        {children}
      </div>
    </section>
  );
}
