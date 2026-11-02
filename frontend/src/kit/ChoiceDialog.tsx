import { useEffect, useRef } from "react";
import { Button } from "./Button";
import { DialogFrame } from "./Dialog";

interface Props {
  title: string;
  message: string;
  /** The answers, in the order shown. A notice has one, such as OK. */
  choices: readonly { id: string; label: string }[];
  /** The answer Enter gives; it has focus when the dialog opens. */
  defaultId: string;
  onChoose: (id: string) => void;
}

/**
 * A question or notice that only a choice dismisses: Escape does nothing and there is no close button. A long
 * message scrolls while the choices stay in view.
 */
export function ChoiceDialog({ title, message, choices, defaultId, onChoose }: Props) {
  const footer = useRef<HTMLDivElement>(null);
  const focusDefault = () => {
    const index = choices.findIndex(({ id }) => id === defaultId);
    (footer.current?.children[index] as HTMLElement | undefined)?.focus();
  };
  // Runs after the frame has opened the dialog, which a closed one would not let take focus.
  useEffect(focusDefault, []);
  return (
    <DialogFrame
      title={title}
      onCancel={(event) => event.preventDefault()}
      // Chromium closes a dialog on a second Escape without a click in between, whatever the first one did.
      onClose={(event) => {
        event.currentTarget.showModal();
        focusDefault();
      }}
      onSubmit={(event) => event.preventDefault()}
      footer={
        <div ref={footer} className="flex flex-wrap justify-center gap-m">
          {choices.map(({ id, label }) => (
            <Button key={id} onClick={() => onChoose(id)}>
              {label}
            </Button>
          ))}
        </div>
      }
    >
      <p className="m-0 max-w-prose min-h-0 overflow-y-auto text-center break-words whitespace-pre-wrap select-text">
        {message}
      </p>
    </DialogFrame>
  );
}
