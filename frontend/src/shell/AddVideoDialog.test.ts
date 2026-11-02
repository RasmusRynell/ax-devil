import { expect, it } from "vite-plus/test";
import type { DecoderInfo } from "../generated/api";
import { decodersFor } from "./AddVideoDialog";

const decoder = (handler: string, ...extensions: string[]): DecoderInfo => ({
  handler,
  display_name: handler,
  extensions,
  options: [],
});

it("picks the decoders whose extension ends the file name, however many dots it holds", () => {
  const decoders = [
    decoder("JSONL", ".jsonl"),
    decoder("ACME", ".acme.jsonl"),
    decoder("MOT", ".txt"),
  ];
  const handlers = (path: string) => decodersFor(decoders, path).map((one) => one.handler);

  expect(handlers("/data/run.ACME.jsonl")).toEqual(["JSONL", "ACME"]);
  expect(handlers("C:\\data\\gt.txt")).toEqual(["MOT"]);
  expect(handlers("/data/notes.mp3")).toEqual(["JSONL", "ACME", "MOT"]);
});
