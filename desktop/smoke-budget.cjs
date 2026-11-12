// Named budgets shared by the app, launchers and CI planning. All values are milliseconds.
const SCENARIO_TIMEOUT_MS = 90_000;
const START_TIMEOUT_MS = 90_000;
const FAILURE_MS = 5000;
const KILL_AFTER_MS = 10_000;
const EXIT_GRACE_MS = 5000;
const APP_START_MS = 30_000;
const { BACKEND_STOP_MS } = require("./child.cjs");
const PACKAGE_EXIT_MS = 10_000;
const PACKAGE_KILL_MS = 1000;
// The POSIX absolute deadline remains armed during verdict shutdown; only escalation and retained-output grace
// can extend it. Windows retains its post-verdict exit/kill envelope. Plan for the larger platform bound.
const LAUNCH_TEARDOWN_MS = Math.max(
  KILL_AFTER_MS + EXIT_GRACE_MS,
  PACKAGE_EXIT_MS + PACKAGE_KILL_MS,
);
const LAUNCH_SLACK_MS = 4000;
const FIXTURE_BUILD_MS = 600_000;
const PACKAGE_SETUP_MS = 60_000;
const LAUNCH_MARGIN_MS =
  APP_START_MS + FAILURE_MS + BACKEND_STOP_MS + KILL_AFTER_MS + EXIT_GRACE_MS + LAUNCH_SLACK_MS;
module.exports = {
  LAUNCH_TEARDOWN_MS,
  PACKAGE_EXIT_MS,
  PACKAGE_KILL_MS,
  SCENARIO_TIMEOUT_MS,
  START_TIMEOUT_MS,
  FAILURE_MS,
  KILL_AFTER_MS,
  EXIT_GRACE_MS,
  FIXTURE_BUILD_MS,
  PACKAGE_SETUP_MS,
  LAUNCH_MARGIN_MS,
};
