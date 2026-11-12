import { act, fireEvent, render, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { expect, it, vi } from "vite-plus/test";
import { FakeSocket } from "../session/fakeSession";
import { scenario } from "../session/fakeScenario";
import { SessionProvider } from "../session/session";
import { SessionStore } from "../session/store";
import { NO_ACTIONS, ViewerActionsContext, type ViewerActions } from "../viewer/actions";
import type { VideoCanvas } from "./VideoCanvas";
import { Viewer, installViewerHarness } from "./videoViewerHarness";

// Keep the real viewer/session/inspection owners; replace only their drawing surfaces.
vi.mock("./VideoCanvas", () => ({
  VideoCanvas: (props: ComponentProps<typeof VideoCanvas>) => (
    <>
      <button onClick={() => props.onSelect?.({ lane: 0, source: 0, entity: "7" })}>Pick A7</button>
      <button onClick={() => props.onSelect?.({ lane: 1, source: 1, entity: "9" })}>Pick B9</button>
    </>
  ),
}));
vi.mock("./Inspector", async (original) => ({
  ...(await original<typeof import("./Inspector")>()),
  Inspector: ({ inspection }: ComponentProps<(typeof import("./Inspector"))["Inspector"]>) => (
    <output aria-label="Inspected object">{JSON.stringify(inspection.selection)}</output>
  ),
}));
installViewerHarness();

it("keeps each Owner seat's tools local while shared overlay toggles cancel both seats' pending picks", async () => {
  const s = scenario("playlist");
  const lanes = [
    { label: "DPM", slot: "DPM", source: 0 },
    { label: "GT", slot: "GT", source: 1 },
  ];
  const seat = () => {
    const store = new SessionStore();
    let actions: ViewerActions = NO_ACTIONS;
    const sent: Parameters<typeof Viewer>[0]["sent"] = [];
    const rendered = render(
      <SessionProvider store={store}>
        <ViewerActionsContext.Provider
          value={(next) => {
            actions = typeof next === "function" ? next(actions) : next;
          }}
        >
          <Viewer sent={sent} lanes={lanes} />
        </ViewerActionsContext.Provider>
      </SessionProvider>,
    );
    const ui = within(rendered.container);
    return { store, ui, tools: () => act(() => actions["media-tools"]?.()) };
  };
  const first = seat();
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1")!;
  act(() => room.show(s));
  await first.ui.findByRole("slider", { name: "Timeline" });
  // Each real window has its own sessionStorage and resume token.
  window.sessionStorage.clear();
  const second = seat();
  await act(async () => {});
  await act(async () => {
    await second.store.attach(room.id);
  });
  await second.ui.findByRole("slider", { name: "Timeline" });
  expect(room.seats.filter((seat) => seat.owner && seat.socket)).toHaveLength(2);
  const presenter = room.presenter;

  first.tools();
  act(() => first.ui.getByRole("button", { name: "Pick A7" }).click());
  second.tools();
  act(() => second.ui.getByRole("button", { name: "Pick B9" }).click());
  const inspection = (seat: typeof first) =>
    JSON.parse(seat.ui.getByLabelText("Inspected object").textContent!);
  expect(inspection(first)).toEqual({ lane: 0, source: 0, entity: "7" });
  expect(inspection(second)).toEqual({ lane: 1, source: 1, entity: "9" });
  expect((first.ui.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).value).toBe("0");
  expect((second.ui.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).value).toBe("1");
  expect(room.presenter).toEqual(presenter);
  fireEvent.change(first.ui.getByRole("combobox", { name: "Lane" }), { target: { value: "1" } });
  expect(first.ui.queryByRole("region", { name: "Object page" })).toBeNull();
  expect(inspection(second)).toEqual({ lane: 1, source: 1, entity: "9" });
  act(() => first.ui.getByRole("button", { name: "Pick A7" }).click());

  first.tools();
  second.tools();
  act(() => first.ui.getByRole("button", { name: "Pick B9" }).click());
  act(() => second.ui.getByRole("button", { name: "Pick A7" }).click());
  await act(async () => {
    await first.store.command({ type: "setOverlay", overlay: "objectDetails", shown: false });
  });
  await act(async () => {
    await second.store.command({ type: "setOverlay", overlay: "objectDetails", shown: true });
  });
  first.tools();
  second.tools();
  expect(first.ui.queryByRole("region", { name: "Object page" })).toBeNull();
  expect(second.ui.queryByRole("region", { name: "Object page" })).toBeNull();
});
