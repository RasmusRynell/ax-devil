import { expect, it } from "vite-plus/test";
import { linesOf } from "./lines";

it("joins lines split across chunks, skips blank ones and ends with an unfinished one", async () => {
  const chunks = ['{"a":1}\n{"b"', ":2}\n\n", '{"c":3}'];
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  });
  const lines: string[] = [];
  for await (const line of linesOf(new Response(body))) lines.push(line);
  expect(lines).toEqual(['{"a":1}', '{"b":2}', '{"c":3}']);
});
