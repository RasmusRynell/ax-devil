# Inspection copy reference

`python-copy.json` was generated from Python ax-devil commit `25f601fa0ea83515062959268ce681d46f04ee34`
using real `Entity`/`Observation`, `entity_fields` and `fields_model._json_of`. Each case carries the equivalent
version-4.1 Scene input and the exact Raw group JSON. Cases include both clocks, non-ASCII/control text, a frame
integer above 2^53, nested diagnostics with floats and empty containers (which stay outside Raw), and empty groups.
Tests consume the committed output and require no Python interpreter or reference checkout.
