const assert = require("node:assert/strict");
const { test } = require("node:test");
const { explainFailure } = require("./remote.cjs");

void test("a failed connection says why in plain words and keeps the code", () => {
  const refused = new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } });
  assert.match(explainFailure(refused), /host may have stopped sharing.*ECONNREFUSED/);
  const silent = new DOMException("The operation was aborted due to timeout", "TimeoutError");
  assert.match(explainFailure(silent), /no answer.*TimeoutError/);
  assert.match(
    explainFailure("net::ERR_ADDRESS_UNREACHABLE"),
    /cannot reach the host.*ERR_ADDRESS_UNREACHABLE/,
  );
});

void test("a failure without a known reason is reported as it came", () => {
  assert.equal(explainFailure("ERR_SOMETHING_NEW"), "ERR_SOMETHING_NEW");
  assert.equal(explainFailure(new Error("odd")), "odd");
  const redirected = new TypeError("fetch failed", { cause: { message: "unexpected redirect" } });
  assert.equal(explainFailure(redirected), "unexpected redirect");
});
