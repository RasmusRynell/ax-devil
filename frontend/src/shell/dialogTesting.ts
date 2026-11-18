/** Whether a dialog's action is waiting: disabled while it works or is refused, `aria-disabled` while a required field is empty. */
export const waits = (button: HTMLElement) =>
  (button as HTMLButtonElement).disabled || button.getAttribute("aria-disabled") === "true";
