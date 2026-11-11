import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { Topic } from "./generated/api";
import { ChangesProvider, useNoticeSink, useResource } from "./resource";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

interface Value {
  text: string;
  revision: number;
}

/** Reads held open until the test settles them, and the way to announce a revision as the Session connection does. */
function harness(topics: Topic | Topic[] = "plugins") {
  const reads: Array<{ resolve: (value: Value) => void; reject: (error: Error) => void }> = [];
  const read = vi.fn(
    () => new Promise<Value>((resolve, reject) => reads.push({ resolve, reject })),
  );
  let notice: (topic: Topic, revision: number, fresh: boolean) => void = () => {};
  let apply: (value: Value) => void = () => {};
  function Probe() {
    const sink = useNoticeSink();
    notice = (topic, revision, fresh) => sink?.(topic, revision, fresh);
    const resource = useResource(topics, "/p", { read, revisionOf: (value) => value.revision });
    apply = resource.apply;
    return <p>{resource.value?.text ?? "none"}</p>;
  }
  render(
    <ChangesProvider>
      <Probe />
    </ChangesProvider>,
  );
  const topic = typeof topics === "string" ? topics : topics[0]!;
  return {
    read,
    answer: (index: number, text: string, revision: number) =>
      act(async () => reads[index]?.resolve({ text, revision })),
    fail: (index: number) => act(async () => reads[index]?.reject(new Error("down"))),
    notice: (revision: number, of: Topic = topic, fresh = false) =>
      act(async () => notice(of, revision, fresh)),
    apply: (text: string, revision: number) => act(async () => apply({ text, revision })),
  };
}

describe("useResource", () => {
  it("reads on mount and for a new revision, never for the one it holds, and once more after a burst of notices", async () => {
    const { read, answer, notice } = harness();
    // Notices faster than the read: it is let finish, and the newest is asked for once.
    await notice(1);
    await notice(2);
    await notice(3);
    expect(read).toHaveBeenCalledTimes(1);
    await answer(0, "first", 1);
    expect(screen.getByText("first")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
    await answer(1, "newest", 3);
    expect(screen.getByText("newest")).toBeTruthy();

    // A reconnect repeats the revision held; a restarted backend announces another one, even a lower one.
    await notice(3, "plugins", true);
    expect(read).toHaveBeenCalledTimes(2);
    await notice(2, "plugins", true);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("ignores a save's answer older than the read it overtook, unless the connection changed", async () => {
    const { read, answer, notice, apply } = harness();
    await answer(0, "initial", 5);
    await notice(6);
    await answer(1, "read", 6);
    await apply("late save", 5);
    expect(screen.getByText("read")).toBeTruthy();
    await apply("newer save", 7);
    expect(screen.getByText("newer save")).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);

    // Another connection: a backend that restarted counts from a lower revision.
    await notice(1, "plugins", true);
    await apply("first save", 2);
    expect(screen.getByText("first save")).toBeTruthy();
  });

  it("reads again for any topic its answer depends on", async () => {
    const { read, answer, notice } = harness(["sessions", "settings"]);
    await answer(0, "list", 0);
    await notice(1, "settings");
    expect(read).toHaveBeenCalledTimes(2);
    await answer(1, "other list", 0);
    await notice(1, "sessions");
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("tries a failed read again with waits, and again for a repeated notice once those are used up", async () => {
    vi.useFakeTimers();
    const { read, answer, fail, notice } = harness();
    await fail(0);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(read).toHaveBeenCalledTimes(2);
    await fail(1);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await answer(2, "recovered", 0);
    expect(screen.getByText("recovered")).toBeTruthy();

    await notice(1);
    await fail(3);
    for (const [index, wait] of [1000, 2000, 4000].entries()) {
      await act(() => vi.advanceTimersByTimeAsync(wait));
      await fail(4 + index);
    }
    expect(read).toHaveBeenCalledTimes(7);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(read).toHaveBeenCalledTimes(7);
    await notice(1);
    expect(read).toHaveBeenCalledTimes(8);
  });
});
