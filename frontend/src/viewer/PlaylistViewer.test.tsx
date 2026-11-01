import { useEffect, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { EntryInfo } from "../generated/api";
import type { ViewerProps } from "../viewers";
import { fakeWorkspace, pending, video } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { ViewerActionsContext, type ViewerActions } from "./actions";
import { hiddenOf, NOTHING_HIDDEN, type Hidden } from "./hidden";
import { adjacentEntry, PlaylistViewer } from "./PlaylistViewer";

// An entry's video viewer that keeps a position of its own in the memory it is given.
vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: ({ content, memory }: ViewerProps<"SeekableVideo">) => {
    const frame = (memory.saved as number | undefined) ?? 0;
    useEffect(() => () => memory.save(frame + 10), []);
    return (
      <p>
        {content.display_name} at {frame}
      </p>
    );
  },
}));

afterEach(cleanup);

const entry = (label: string): EntryInfo => ({ id: `list/${label}`, label, lanes: [label] });
const ENTRIES = ["A", "B", "C", "D"].map(entry);

const hiding = (...labels: string[]): Hidden =>
  hiddenOf(
    labels.map((label) => ({
      key: `list/${label}`,
      kind: "entry" as const,
      label,
      status: { status: "Ready" as const },
      hidden: true,
      entry: `list/${label}`,
    })),
  );

function mount(hidden: Hidden = NOTHING_HIDDEN, saved?: unknown, entries = ENTRIES) {
  const workspace = fakeWorkspace();
  const registry: { current: ViewerActions } = { current: {} };
  const memory = { saved, save: vi.fn() };
  const content = { kind: "Playlist" as const, id: "list", display_name: "list", entries };
  const element = (shown: Hidden) => (
    <WorkspaceProvider store={workspace.store}>
      <ViewerActionsContext.Provider value={registry}>
        <PlaylistViewer content={content} memory={memory} hidden={shown} />
      </ViewerActionsContext.Provider>
    </WorkspaceProvider>
  );
  const view = render(element(hidden));
  act(() => workspace.source().send([video("list", true, pending)]));
  const run = (id: string) => act(() => registry.current[id]?.());
  return {
    run,
    registry,
    memory,
    workspace,
    ...view,
    show: (shown: Hidden) => view.rerender(element(shown)),
  };
}

describe("adjacent entry", () => {
  it("skips hidden entries and stops at the ends", () => {
    const hidden = hiding("B", "C");
    expect(adjacentEntry(ENTRIES, hidden, 0, 1)?.label).toBe("D");
    expect(adjacentEntry(ENTRIES, hidden, 3, -1)?.label).toBe("A");
    expect(adjacentEntry(ENTRIES, hidden, 0, -1)).toBeUndefined();
    expect(adjacentEntry(ENTRIES, hiding("D"), 2, 1)).toBeUndefined();
  });
});

describe("playlist viewer", () => {
  it("opens the first entry that is not hidden", () => {
    mount(hiding("A"));
    expect(screen.getByText("B at 0")).toBeTruthy();
  });

  it("moves between entries with its buttons and with the commands, skipping hidden ones", () => {
    const { run } = mount(hiding("B"));
    expect(screen.getByText("A at 0")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Previous entry" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Next entry" }));
    expect(screen.getByText("C at 0")).toBeTruthy();
    expect(screen.getByLabelText("Entry").textContent).toBe("3 / 4");
    run("next-entry");
    expect(screen.getByText("D at 0")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next entry" })).toHaveProperty("disabled", true);
    run("previous-entry");
    run("previous-entry");
    expect(screen.getByText("A at 10")).toBeTruthy();
  });

  it("offers no command where there is no entry to go to", () => {
    const { registry } = mount();
    expect(registry.current["previous-entry"]).toBeUndefined();
    expect(registry.current["next-entry"]).toBeTypeOf("function");
  });

  it("picks an entry from its menu, which lists the entries that are not hidden", () => {
    mount(hiding("C"));
    fireEvent.click(screen.getByRole("button", { name: "Entry: A" }));
    expect(screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)).toEqual([
      "A",
      "B",
      "D",
    ]);
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "D" }));
    expect(screen.getByText("D at 0")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("returns every entry to where it was left, and to the entry it showed", () => {
    const first = mount();
    fireEvent.click(screen.getByRole("button", { name: "Next entry" }));
    first.unmount();
    // The viewer saved the entry and, as each video went away, its position.
    const saved = first.memory.save.mock.calls.at(-1)?.[0];
    expect(saved).toEqual({ entry: "list/B", positions: { "list/A": 10, "list/B": 10 } });
    mount(NOTHING_HIDDEN, saved);
    expect(screen.getByText("B at 10")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous entry" }));
    expect(screen.getByText("A at 10")).toBeTruthy();
  });

  it("keeps the entry on screen when it is hidden, and leaves it by the next visible one", () => {
    const { show, run } = mount();
    run("next-entry");
    show(hiding("B", "C"));
    expect(screen.getByText("B at 0")).toBeTruthy();
    run("next-entry");
    expect(screen.getByText("D at 0")).toBeTruthy();
  });

  it("shows the entry the workspace tree asked for, even a hidden one, and says it did", () => {
    const { workspace } = mount(hiding("C"));
    act(() => workspace.store.show("list", "list/C"));
    expect(screen.getByText("C at 0")).toBeTruthy();
    expect(workspace.store.getSnapshot().requested).toBeNull();
  });

  it("shows an entry that was asked for before it opened", () => {
    const workspace = fakeWorkspace();
    const content = {
      kind: "Playlist" as const,
      id: "list",
      display_name: "list",
      entries: ENTRIES,
    };
    const page = (child: ReactNode) => (
      <WorkspaceProvider store={workspace.store}>{child}</WorkspaceProvider>
    );
    const { rerender } = render(page(null));
    act(() => workspace.source().send([video("list", true, pending)]));
    act(() => workspace.store.show("list", "list/D"));
    rerender(
      page(
        <PlaylistViewer
          content={content}
          memory={{ saved: undefined, save: vi.fn() }}
          hidden={NOTHING_HIDDEN}
        />,
      ),
    );
    expect(screen.getByText("D at 0")).toBeTruthy();
    expect(workspace.store.getSnapshot().requested).toBeNull();
  });

  it("says why there is nothing to show", () => {
    mount(hiding("A", "B", "C", "D"));
    expect(screen.getByRole("status").textContent).toBe("All entries are hidden");
    cleanup();
    mount(NOTHING_HIDDEN, undefined, []);
    expect(screen.getByRole("status").textContent).toBe("This playlist has no entries");
  });
});
