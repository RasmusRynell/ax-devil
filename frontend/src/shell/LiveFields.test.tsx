import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DEFAULT_SETTINGS } from "../fakeSettings";
import { ConnectionRows } from "./LiveFields";
import { formValues, startForm, type LiveForm } from "./liveForm";

afterEach(cleanup);

const values = formValues(DEFAULT_SETTINGS);

function show(settings = values, change: Partial<LiveForm> = {}) {
  const set = vi.fn();
  render(
    <ConnectionRows
      form={{ ...startForm(settings), ...change }}
      set={set}
      settings={settings}
      expansions={null}
      hidden={null}
      resolutions={{ state: { phase: "idle" }, reload: () => {} }}
      sizes={[]}
      hostUsable={false}
    />,
  );
  return set;
}

const port = () => screen.getByLabelText("RTSP Port") as HTMLInputElement;

describe("the RTSP route of the connection rows", () => {
  it("offers the configured route as the default and sends the one chosen", () => {
    const set = show();
    const route = screen.getByLabelText("RTSP Route") as HTMLSelectElement;
    expect([...route.options].map((option) => option.textContent)).toEqual([
      "Default (Direct)",
      "Direct",
      "Via web API",
    ]);
    fireEvent.change(route, { target: { value: "web_api" } });
    expect(set).toHaveBeenCalledWith("rtsp_route", "web_api");
  });

  it("leaves the RTSP port out while the web API carries the stream, whether chosen or configured", () => {
    show();
    expect(port().disabled).toBe(false);
    cleanup();
    show(values, { rtsp_route: "web_api" });
    expect(port().disabled).toBe(true);
    cleanup();
    show({ ...values, "camera.rtsp_route": "web_api" });
    expect(port().disabled).toBe(true);
    expect((screen.getByLabelText("RTSP Route") as HTMLSelectElement).options[0]?.textContent).toBe(
      "Default (Via web API)",
    );
    cleanup();
    show({ ...values, "camera.rtsp_route": "web_api" }, { rtsp_route: "direct" });
    expect(port().disabled).toBe(false);
  });
});
