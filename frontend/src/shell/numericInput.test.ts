import { expect, it } from "vite-plus/test";
import { numericInput } from "./numericInput";

it.each([4300, 4301])(
  "matches Python's integer digit limit at %i digits, without imposing it on float input",
  (digits) => {
    const integer = `${"0".repeat(digits - 1)}5`;
    expect(numericInput(integer, "whole")).toBe(digits === 4300 ? 5 : integer);
    expect(numericInput(` +${integer} `, "whole")).toBe(digits === 4300 ? 5 : `+${integer}`);
    expect(numericInput(integer, "auto_or_whole")).toBe(5);
    expect(numericInput(`${integer}.0`, "auto_or_whole")).toBe(5);
  },
);
