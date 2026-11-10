// @vitest-environment node
import { describe, expect, it } from "vite-plus/test";
import { OffsetEstimator } from "./timeSync";

describe("time sync", () => {
  it("trusts the lowest round trip and converts milliseconds to microseconds", () => {
    const estimator = new OffsetEstimator();
    expect(estimator.serverNowUs(0)).toBeUndefined();
    // Server is 5 s ahead. The slow, asymmetric sample would suggest 5.4 s.
    estimator.add({ sentUs: 1_000_000, receivedUs: 1_800_000, serverUs: 6_800_000 });
    estimator.add({ sentUs: 2_000_000, receivedUs: 2_010_000, serverUs: 7_005_000 });
    expect(estimator.serverNowUs(3000)).toBe(8_000_000);
  });

  it("forgets samples older than its window", () => {
    const estimator = new OffsetEstimator();
    estimator.add({ sentUs: 0, receivedUs: 1000, serverUs: 10_500 });
    for (let i = 0; i < 8; i++) {
      estimator.add({ sentUs: 0, receivedUs: 50_000, serverUs: 25_000 });
    }
    expect(estimator.serverNowUs(0)).toBe(0);
  });
});
