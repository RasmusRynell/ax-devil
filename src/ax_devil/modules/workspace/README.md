# workspace module

Purpose: the Workspace's items and file format (`core/`) and the sidebar views that list and act on them (`ui/`). The
package split is described in [Workspace](../../../../docs/architecture/workspace.md).

## Sidebar view shell

`SectionsView` is the one shell for the Workspace, Live and Offline views, with a fixed layout: a toolbar (Search, Sort,
Filter, in that order), the tree, then an optional footer panel. A view never adds toolbar widgets or lays out its own
footer; it fills these slots.

- Sort offers the `RowOrder`s the `SectionsViewController` is given (declared in `browser_rows.py`, default
  `WORKSPACE_ORDER` and `BY_NAME`). It shows with two or more.
- Filter shows only when a facet group has a choice to make.
- A view adds a panel, such as Live's scan settings, with `set_footer_panel`, directly on itself. A button that acts on
  a section, such as Live's Add stream, is the section's `SectionAction`, shown beside its caption.
