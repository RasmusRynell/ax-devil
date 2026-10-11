# Workspace consolidation screenshots

Application revision: `8795e7c31f842841de23e3b64b99b7997d0f1498`.
Before revision: `2cee4c1` (main).

Captured with `tools/ui_screenshots.py` through pytest in an isolated temporary home on Qt's offscreen platform.
The video and analytics are generated fixtures; cameras use a stand-in viewer without network connections.

The start comparisons put main on the left and the consolidated change on the right. Full-size images cover
dark/light themes, medium/larger text, the start panel, grouped mixed content, and add-content dialogs.

Command, run in each corresponding checkout with a different output folder:

```bash
PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/ax-devil-consolidation-after-shots \
  uv run pytest -p tests.conftest tools/ui_screenshots.py -q -k test_screenshots
```
