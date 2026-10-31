import type { ReactNode } from "react";
import { IconButton } from "../kit/IconButton";

interface Props {
  /** `alert` for something that went wrong where the user acted, `status` for a state that may pass. */
  role: "alert" | "status";
  children: ReactNode;
  onDismiss?: () => void;
}

/** A line across the top of the window for what the user should know now. */
export function Banner({ role, children, onDismiss }: Props) {
  return (
    <div
      role={role}
      className="flex min-h-row shrink-0 items-center gap-m border-b border-line bg-popup pl-m"
    >
      <p className="m-0 min-w-0 flex-1 py-xs break-words">{children}</p>
      {onDismiss && (
        <IconButton
          icon="x"
          label="Dismiss"
          onClick={onDismiss}
          className="size-row rounded-control"
        />
      )}
    </div>
  );
}
