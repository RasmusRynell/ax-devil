import { Icon } from "./Icon";
import { TONE } from "./look";

/** Why the backend refused a value, shown at the value in the danger tone; `title` is the fuller reason on hover. */
export function Refusal({ reason, title }: { reason: string; title?: string }) {
  return (
    <p role="alert" title={title} className={`m-0 flex items-center gap-s ${TONE.danger.text}`}>
      <Icon name="triangle-alert" />
      <span className="min-w-0 [overflow-wrap:anywhere]">{reason}</span>
    </p>
  );
}
