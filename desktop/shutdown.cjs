// One terminal shutdown sequence, shared by quit/restart/signals through the main lifecycle.
function createShutdown({ device, backend, exit }) {
  let outcome;
  return () => {
    outcome ??= device
      .close()
      .then(() => backend.stop())
      .then(exit);
    return outcome;
  };
}
module.exports = { createShutdown };
