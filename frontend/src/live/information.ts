/** What the Information dialog of a stream lists: its settings as saved, never a password. */

import type { NewLiveOverlayWire, NewLiveWire } from "../generated/api";
import type { Stream } from "./cameras";

/** A titled list of labelled values, with lists under it. */
export type Information = {
  title: string;
  fields: readonly (readonly [string, string])[];
  children: readonly Information[];
};

const SOURCES: Record<NewLiveOverlayWire["mode"], string> = {
  none: "None",
  rtsp: "RTSP",
  mqtt: "MQTT",
  datahub: "DataHub WebSocket",
};

/** The settings of the overlay's transport. */
function overlayFields(overlay: NewLiveOverlayWire): [string, string][] {
  const handler: [string, string] = [
    "handler type",
    overlay.mode === "none" ? "" : (overlay.handler ?? ""),
  ];
  switch (overlay.mode) {
    case "none":
      return [];
    case "rtsp":
      return [handler];
    case "mqtt":
      return [
        handler,
        ["broker host", overlay.broker_host ?? ""],
        ["broker port", String(overlay.broker_port ?? "")],
        ["broker username", overlay.broker_user || "(none)"],
        ["data source", overlay.source_key ?? ""],
      ];
    case "datahub":
      return [
        handler,
        ["topic", overlay.topic ?? ""],
        ["channel ID", String(overlay.channel ?? "")],
      ];
  }
}

/** The camera and stream settings of a recipe, as the stream was saved. */
function connectionFields(recipe: NewLiveWire): [string, string][] {
  return [
    ["host", recipe.host ?? ""],
    ["username", recipe.username ?? ""],
    ["camera head", String(recipe.head ?? "")],
    ["resolution", recipe.resolution ?? ""],
    ["stream URL", recipe.url || "(default)"],
    ["device API protocol", (recipe.protocol ?? "").toUpperCase()],
    ["device port", recipe.port ? String(recipe.port) : "(standard)"],
    ["RTSP port", String(recipe.rtsp_port ?? "")],
  ];
}

/** The information of `stream`: why it is unavailable, or its settings and, under them, its overlay lane. */
export function informationOf(stream: Stream): Information {
  const { label, recipe } = stream;
  if (stream.state !== "ready")
    return { title: label, fields: [["Unavailable", stream.reason]], children: [] };
  const overlay = recipe?.overlay;
  const draws = overlay && overlay.mode !== "none";
  return {
    title: label,
    fields: [
      ["type", "live video"],
      ["overlays", draws ? "1" : "0"],
      ...(recipe ? connectionFields(recipe) : []),
    ],
    children: draws
      ? [
          {
            title: stream.lane ?? SOURCES[overlay.mode],
            fields: [
              ["type", "overlay"],
              ["video", label],
              ["lane", stream.lane ?? ""],
              ["overlay source", SOURCES[overlay.mode]],
              ...overlayFields(overlay),
            ],
            children: [],
          },
        ]
      : [],
  };
}
