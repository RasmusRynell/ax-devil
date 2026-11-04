/** The characters at the end of a text that always show. */
const TAIL = 8;

/**
 * A text on one line that loses its middle where it does not fit, as an id does: its end is what tells it from the
 * others. The whole text is the title unless `title` says otherwise; a text of 16 characters or fewer loses its end.
 */
export function MiddleElided({
  text,
  title = text,
  className = "",
}: {
  text: string;
  title?: string;
  className?: string;
}) {
  return (
    <span title={title} className={`flex min-w-0 ${className}`}>
      {text.length > 2 * TAIL ? (
        <>
          <span className="truncate">{text.slice(0, -TAIL)}</span>
          <span className="shrink-0">{text.slice(-TAIL)}</span>
        </>
      ) : (
        <span className="truncate">{text}</span>
      )}
    </span>
  );
}
