/** The non-blank lines of a streamed answer, as they arrive; a last line with no newline comes when the stream ends. */
export async function* linesOf(response: Response): AsyncGenerator<string> {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let pending = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const parts = (pending + decoder.decode(value, { stream: true })).split("\n");
    pending = parts.pop() ?? "";
    yield* parts.filter((line) => line.trim() !== "");
  }
  if (pending.trim() !== "") yield pending;
}
