import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { GrantsContext } from "../access";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { SessionsView } from "./SessionsView";
import { ShareControl } from "./ShareControl";

const writeText = vi.fn(async () => {});

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => [{ id: "ann", ownerPresent: true, participants: 1 }],
    })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  writeText.mockClear();
});

describe("sessions view", () => {
  it("starts sharing, copies the join link, lets viewers explore, and stopping makes the window private again", async () => {
    render(
      <GrantsContext value={["sessions"]}>
        <SessionProvider>
          <ShareControl onStartSharing={() => {}} />
          <SessionsView />
        </SessionProvider>
      </GrantsContext>,
    );
    expect(await screen.findByRole("button", { name: "Watch Session ann" })).toBeTruthy();
    // Connecting to a server is the desktop shell's: a plain browser is not offered it.
    expect(screen.queryByRole("button", { name: "Connect to server" })).toBeNull();

    const own = () => within(screen.getByRole("region", { name: "Session" }));
    fireEvent.click(own().getByRole("button", { name: "Start sharing" }));
    await screen.findByRole("button", { name: "Stop sharing" });
    const session = own();
    expect(session.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Guest 1 (you)",
    ]);
    const link = (session.getByLabelText("Join link") as HTMLInputElement).value;
    expect(link).toContain("#join=");
    fireEvent.click(session.getByRole("button", { name: "Copy join link" }));
    expect(writeText).toHaveBeenCalledWith(link);

    // Viewers explore only while the owner allows it; the backend's answer is what the box shows.
    fireEvent.click(session.getByRole("checkbox", { name: "Let viewers explore on their own" }));
    await act(async () => {});
    expect(FakeSocket.rooms.get("room-1")?.access).toBe("explore");
    expect((session.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);

    // The owner sees which guest explores, and none once exploring is off (everyone follows then).
    const room = FakeSocket.rooms.get("room-1")!;
    act(() => {
      room.seat(null);
      room.seat(null).view = room.presenter;
      room.share();
    });
    const exploring = session.getAllByText("Exploring");
    expect(exploring.map((tag) => tag.closest("li")?.textContent)).toEqual(["Guest 3Exploring"]);
    expect(screen.getByRole("button", { name: "Session" }).title).toBe("2 watching, 1 exploring");
    fireEvent.click(session.getByRole("checkbox", { name: "Let viewers explore on their own" }));
    await act(async () => {});
    expect(session.queryByText("Exploring")).toBeNull();

    // The cursor option is this person's own, sent to the backend.
    fireEvent.click(screen.getByRole("button", { name: "Session" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Share my cursor" }));
    expect(FakeSocket.latest.received).toContainEqual({
      type: "cursorOptions",
      show: true,
      share: false,
    });

    // A backend other machines cannot reach has no link, and says why.
    act(() =>
      FakeSocket.latest.emit({
        type: "seat",
        rev: 1,
        docRev: 0,
        presenterRev: 0,
        access: "owner",
        can: { view: true, content: true, host: true, control: true },
        mode: "following",
        sharing: { access: "explore" },
        view: { sidebar: { view: "workspace", open: true }, viewers: {} },
        viewsDone: 0,
      }),
    );
    expect(session.getByRole("button", { name: "Copy join link" }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(session.getByText("Join links need the release build (make build)")).toBeTruthy();

    fireEvent.click(session.getByRole("button", { name: "Stop sharing" }));
    expect(await own().findByRole("button", { name: "Start sharing" })).toBeTruthy();
  });

  it("shows a watcher whose presentation it is, offers Follow presenter while it explores, and never offers editing", async () => {
    const room = FakeSocket.host("explore");
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <ShareControl onStartSharing={() => {}} />
        <SessionsView />
      </SessionProvider>,
    );
    expect((await screen.findByText(/Presenting:/)).textContent).toBe("Presenting: Guest 1");
    expect(screen.queryByRole("button", { name: "Follow presenter" })).toBeNull();
    expect(screen.getByRole("button", { name: "Leave" })).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();

    act(() => {
      room.seats[1]!.view = room.presenter;
      room.share();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Follow presenter" }));
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "Follow presenter" })).toBeNull();
  });
});
