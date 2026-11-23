// CI consumes this calculation instead of maintaining a second list of groups or timeout totals.
const { loadScenarios, deadlineMs } = require("./smoke-desktop.cjs");
const groups = require("../desktop/smoke-groups.cjs");
const {
  FIXTURE_BUILD_MS,
  PACKAGE_SETUP_MS,
  LAUNCH_TEARDOWN_MS,
  EXIT_GRACE_MS,
} = require("../desktop/smoke-budget.cjs");
const scenarios = loadScenarios(groups);
const launches = Object.entries(groups).map(([group, names]) => ({
  group,
  deadline: deadlineMs(names, scenarios),
  teardown: LAUNCH_TEARDOWN_MS,
}));
const inner =
  FIXTURE_BUILD_MS +
  EXIT_GRACE_MS +
  PACKAGE_SETUP_MS +
  launches.reduce((sum, row) => sum + row.deadline + row.teardown, 0);
const smokeMinutes = Math.ceil(inner / 60000) + 1;
const result = {
  launches,
  fixtureBuildSeconds: FIXTURE_BUILD_MS / 1000,
  setupSeconds: PACKAGE_SETUP_MS / 1000,
  innerSeconds: inner / 1000,
  smokeMinutes,
  jobMinutes: smokeMinutes + 30,
};
if (process.argv.includes("--github")) {
  for (const key of ["smokeMinutes", "jobMinutes"]) console.log(`${key}=${result[key]}`);
} else console.log(JSON.stringify(result, null, 2));
