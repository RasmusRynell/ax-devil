import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { ChangesProvider, useNoticeSink, useResource } from "./resource";

afterEach(cleanup);

/** Reads held open until the test answers them, and the way to announce a revision as the Session connection does. */
function harness() {
  const reads: Array<(value: string) => void> = [];
  const read = vi.fn(() => new Promise<string>((resolve) => reads.push(resolve)));
  let notice: (revision: number) => void = () => {};
  let apply: (value: string) => void = () => {};
  function Probe() {
    const sink = useNoticeSink();
    notice = (revision) => sink?.("plugins", revision);
    const resource = useResource("plugins", "/p", { read });
    apply = resource.apply;
    return <p>{resource.value ?? "none"}</p>;
  }
  render(
    <ChangesProvider>
      <Probe />
    </ChangesProvider>,
  );
  return {
    read,
    answer: (index: number, value: string) => act(async () => reads[index]?.(value)),
    notice: (revision: number) => act(async () => notice(revision)),
    apply: (value: string) => act(async () => apply(value)),
  };
}

describe("useResource", () => {
  it("reads on mount, once more for each new revision announced, and never for the one it holds", async () => {
    const { read, answer, notice } = harness();
    expect(read).toHaveBeenCalledTimes(1);
    await notice(5);
    await answer(1, "after 5");
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.getByText("after 5")).toBeTruthy();

    // A reconnect repeats the revision held; a restarted backend announces another one, even a lower one.
    await notice(5);
    expect(read).toHaveBeenCalledTimes(2);
    await notice(2);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("drops an answer that a newer read or a later change overtook", async () => {
    const { read, answer, notice, apply } = harness();
    await notice(1);
    await notice(2);
    expect(read).toHaveBeenCalledTimes(3);
    await answer(2, "newest");
    await answer(1, "older");
    await answer(0, "oldest");
    expect(screen.getByText("newest")).toBeTruthy();

    await notice(3);
    await apply("saved");
    await answer(3, "read before the save");
    expect(screen.getByText("saved")).toBeTruthy();
  });
});
