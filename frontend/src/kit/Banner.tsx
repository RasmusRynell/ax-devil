import type { ReactNode } from "react";
import { Icon } from "./Icon";
import { IconButton } from "./IconButton";
import { TONE } from "./look";

interface Props {
  /** `alert` for something that went wrong where the user acted, `status` for a state that may pass. */
  role: "alert" | "status";
  children: ReactNode;
  /** Buttons that act on the message. They share its row while it fits on one line, else sit right-aligned under it. */
  actions?: ReactNode;
  onDismiss?: () => void;
}

/** A card across the top of the window for what the user should know now. */
export function Banner({ role, children, actions, onDismiss }: Props) {
  const tone = TONE[role === "alert" ? "danger" : "neutral"];
  return (
    <div
      role={role}
      className={`m-s flex min-h-row shrink-0 items-center gap-x-m gap-y-s rounded-popup border bg-popup px-s ${tone.edge} ${actions ? "flex-wrap" : ""}`}
    >
      <div className="flex min-w-0 flex-auto items-start gap-m">
        <span className={`mt-xs flex h-lh items-center ${tone.text}`}>
          <Icon name={role === "alert" ? "triangle-alert" : "info"} />
        </span>
        <p className="m-0 min-w-0 flex-auto py-xs break-words">{children}</p>
      </div>
      {(actions || onDismiss) && (
        <div className="ml-auto flex flex-wrap items-center justify-end gap-m">
          {actions}
          {onDismiss && <IconButton icon="x" label="Dismiss" onClick={onDismiss} />}
        </div>
      )}
    </div>
  );
}
