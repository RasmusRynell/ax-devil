import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vite-plus/test";
import { waitFor, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AccessContext } from "../client/access";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import { SettingsDialog } from "./SettingsDialog";

// The dialog built from the real settings table, which SettingsDialog.test.tsx replaces with one of its own.

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
});
afterEach(cleanup);
afterAll(() => vi.restoreAllMocks());

const open = () =>
  render(
    <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
      <FakeSettings state={DEFAULT_SETTINGS}>
        <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
      </FakeSettings>
    </AccessContext.Provider>,
  );

describe("settings dialog, real table", () => {
  it("lists the device, analytics and scan defaults on their pages", () => {
    open();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "General",
      "Playback",
      "Stream defaults",
      "Analytics",
      "Network scan",
      "Storage",
    ]);
    // Each row's tooltip is its setting key, so the keys give the order, and the headings the groups.
    const rows = (page: string) => {
      fireEvent.click(screen.getByRole("tab", { name: page }));
      const panel = screen.getByRole("tabpanel");
      return [
        within(panel)
          .queryAllByRole("heading")
          .map((heading) => heading.textContent),
        Array.from(panel.querySelectorAll("[title]"))
          .map((row) => row.getAttribute("title"))
          .filter((title) => /^[a-z]+\.[a-z_]+$/.test(title ?? "")),
      ];
    };
    expect(rows("Stream defaults")).toEqual([
      ["Device", "Video and overlay"],
      [
        "camera.protocol",
        "camera.host",
        "camera.user",
        "camera.password",
        "camera.rtsp_port",
        "camera.rtsp_route",
        "camera.timeout",
        "camera.head",
        "camera.overlay",
        "camera.rtsp_handler",
      ],
    ]);
    expect(rows("Analytics")).toEqual([
      ["MQTT analytics", "DataHub WebSocket analytics"],
      [
        "broker.host",
        "broker.port",
        "broker.user",
        "broker.password",
        "broker.source_key",
        "broker.handler",
        "datahub.topic",
        "datahub.channel",
        "datahub.handler",
      ],
    ]);
    expect(rows("Network scan")).toEqual([
      ["Discovery", "Network scan", "Sign-in logins"],
      ["scan.on_startup", "scan.sign_in", "scan.range", "scan.logins"],
    ]);
  });

  it("masks a literal password and warns about it, and shows a $VARIABLE reference as it is", () => {
    open();
    for (const [page, label] of [
      ["Stream defaults", "Password"],
      ["Analytics", "Broker password"],
    ]) {
      fireEvent.click(screen.getByRole("tab", { name: page }));
      const password = screen.getByLabelText(label) as HTMLInputElement;
      expect(password.type).toBe("text");
      expect(screen.queryByText(/^Saved as plain text/)).toBeNull();
      fireEvent.change(password, { target: { value: "hunter2" } });
      expect(password.type).toBe("password");
      expect(screen.getByText(/^Saved as plain text/).id).toBe(
        password.getAttribute("aria-describedby"),
      );
    }
  });
});

it.each([
  [" +5 ", 5, "+5.12e2", 512],
  ["1_024", 1024, "512.", 512],
  ["5e0", "5e0", "5_12.0", 512],
  ["5.", "5.", "512.5", 512.5],
  ["1__2", "1__2", "1__2", "1__2"],
  [" -5 ", -5, ".5", 0.5],
  [" +٥ ", 5, "٥١٢.", 512],
  ["\u00855\u0085", 5, "512e+0", 512],
  ["\uFEFF5", "\uFEFF5", "nan", "nan"],
])(
  "normalizes numeric input %s on submit, preserving references and password whitespace",
  async (head, parsedHead, cache, parsedCache) => {
    const apply = vi.fn().mockResolvedValue(null);
    render(
      <AccessContext.Provider value={{ owner: true, fullAccess: true }}>
        <FakeSettings apply={apply}>
          <SettingsDialog onClose={() => {}} onRestartNeeded={() => {}} />
        </FakeSettings>
      </AccessContext.Provider>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Stream defaults" }));
    for (const [label, value] of [
      ["RTSP port", " $PORT "],
      ["Camera head", head],
      ["Password", " secret "],
    ])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("tab", { name: "Playback" }));
    fireEvent.change(screen.getByLabelText("Memory for video caching (MiB)"), {
      target: { value: cache },
    });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() =>
      expect(apply).toHaveBeenCalledWith(
        expect.objectContaining({
          set: expect.objectContaining({
            "camera.rtsp_port": "$PORT",
            "camera.head": parsedHead,
            "playback.video_cache": parsedCache,
            "camera.password": " secret ",
          }),
        }),
      ),
    );
  },
);
