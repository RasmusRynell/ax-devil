# Inspection copy reference

`python-copy.json` was generated from Python ax-devil commit `25f601fa0ea83515062959268ce681d46f04ee34`
using real `Entity`/`Observation`, `entity_fields` and `fields_model._json_of`. Each case carries the equivalent
version-4.1 Scene input and the exact Raw group JSON. Cases include both clocks, non-ASCII/control text, a frame
integer above 2^53, nested diagnostics with floats and empty containers (which stay outside Raw), and empty groups.
Tests consume the committed output and require no Python interpreter or reference checkout.

Regenerate with `generate.py` using the reference checkout above and its Python environment (including PySide6):

```bash
scripts/isolated "$REFERENCE_PYTHON" fixtures/inspection/generate.py "$REFERENCE_CHECKOUT" artifacts/python-copy.json
```

Compare `artifacts/python-copy.json` with the committed file, then replace it if the reference intentionally changes.
The original generation used Python 3.10.22; review reproduced identical bytes under Python 3.12.14.
The script disables reference bytecode writes and emits UTF-8 with LF on every platform. It is not a CI dependency.
