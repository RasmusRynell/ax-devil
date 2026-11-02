import type { ReactNode } from "react";
import { IconButton } from "../kit/IconButton";

interface Props {
  /** `alert` for something that went wrong where the user acted, `status` for a state that may pass. */
  role: "alert" | "status";
  children: ReactNode;
  /** Buttons that act on the message. They share its row while it fits on one line, else sit right-aligned under it. */
  actions?: ReactNode;
  onDismiss?: () => void;
}

/** A line across the top of the window for what the user should know now. */
export function Banner({ role, children, actions, onDismiss }: Props) {
  return (
    <div
      role={role}
      className={`flex min-h-row shrink-0 items-center gap-x-m gap-y-s border-b border-line bg-popup pl-m ${actions ? "flex-wrap" : ""}`}
    >
      <p className="m-0 min-w-0 flex-auto py-xs break-words">{children}</p>
      {(actions || onDismiss) && (
        <div className="ml-auto flex flex-wrap items-center justify-end gap-m">
          {actions}
          {onDismiss && (
            <IconButton
              icon="x"
              label="Dismiss"
              onClick={onDismiss}
              className="size-row rounded-control"
            />
          )}
        </div>
      )}
    </div>
  );
}
