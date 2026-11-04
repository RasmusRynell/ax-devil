import { Button } from "../kit/Button";
import { IconButton } from "../kit/IconButton";
import { MiddleElided } from "../kit/MiddleElided";
import type { Steps } from "./pageStack";

/**
 * The header of a page in the media tools: Back, named after the page below; the page's title; and the objects
 * before and after it in the list it came from. A step with no object is disabled.
 */
export function PageHeader({
  title,
  below,
  steps,
  onBack,
  onStep,
}: {
  title: string;
  /** The title of the page Back returns to. */
  below: string;
  steps: Steps;
  onBack: () => void;
  onStep: (entity: string) => void;
}) {
  const back = `Back (Esc) to ${below}`;
  return (
    <header className="flex items-center gap-s border-b border-line py-xs pr-xs pl-m">
      <Button
        size="small"
        icon="step-back"
        aria-label={`Back to ${below}`}
        title={back}
        className="max-w-1/3 min-w-0 shrink"
        onClick={onBack}
      >
        <MiddleElided text={below} title={back} />
      </Button>
      <h2 className="m-0 flex min-w-0 flex-1 px-s font-semibold">
        <MiddleElided text={title} />
      </h2>
      <IconButton
        icon="chevron-up"
        label="Previous in list"
        disabled={steps.previous === null}
        onClick={() => steps.previous !== null && onStep(steps.previous)}
      />
      <IconButton
        icon="chevron-down"
        label="Next in list"
        disabled={steps.next === null}
        onClick={() => steps.next !== null && onStep(steps.next)}
      />
    </header>
  );
}
