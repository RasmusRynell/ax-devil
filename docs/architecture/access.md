# Access And Settings

Who may do what, and where each setting lives. Sharing, `serve` and remote windows are new in the rewrite, so this
page is the reference for them, not the Python app.

## Five Facts

Every access or settings decision reads exactly one of these. Never derive one from another.

| Fact | Values | Known by | Decides |
|------|--------|----------|---------|
| Role | Owner, Guest | the backend, from the credential | what a caller may do |
| Full access | yes, no | the backend | whether an Owner may reach outside ax-devil on the server's machine |
| Seat | one connection's place in one Session | the backend | what a caller sees |
| Scope | Server, Personal, Device | the settings table | where a setting is stored and who gets it |
| Native features | what the shell offers this window | the desktop shell | file dialogs, restart, opening logs |

## Role

- **Owner**: a connection through the local listener, or one that presents the Server Password. The Owner is one
  person on any number of devices; every Owner connection is the same Owner. There is no per-device identity.
- **Guest**: the holder of a Join Link. A Guest exists inside that one Session and has no access outside it.
- There is no third role.

## Full Access

- A local connection always has it. A Server Password holder has it only while the Server setting
  `server.password_full_access` is on; it is off by default.
- It is needed for exactly these: installing, updating or removing a plugin; resolving a `$NAME` reference or using a
  login configured on the server; changing a settings row marked `full_access`; changing any setting to a `$NAME`
  reference.
- Without it an Owner supplies their own logins, and a `$NAME` in their Session is text. A Session reads the server's
  environment only if it was created with Full access; it keeps that when the setting changes.
- A settings save is admitted before anything expands a `$NAME`, and a caller without Full access is validated and
  answered as written, so a refusal never carries what the environment holds.
- The test for anything new: it needs Full access if it can run code on the server's machine, read its
  environment, or change where ax-devil reads or writes files. Otherwise Owner is enough.

## Routes

Each route row names who may call it: **Public**, **Owner**, **Full access**, or **Local** (meaningful only on
the server's own machine: the launch hand-off). A session socket is seated by its first message.
No handler checks an address, a listener or a list of grants itself.

## Sessions And Seats

- A Session has one Presenter View. Every Owner seat drives it and shows it; an Owner has no private view.
- A Guest seat follows the Presenter View, or explores its own view when the Sharing options allow it.
- Sharing options are what the Owner allows every Guest of a Session (Watch or Explore). They are Session state,
  never settings.
- An Owner who opens a server attaches to one of its Sessions as an Owner seat, or starts a new one; the one
  exception is a Session that reads the server's environment, which an Owner without Full access attaches to as a
  Guest.
- A Session lives while an Owner seat is attached. When the last one disconnects the grace period runs; when the
  last one leaves, the Session ends. A Guest never keeps a Session alive.
- A Guest receives a projection of the Session: never a setting, a credential, a reference, a host path or the
  detail of a failure.
- A viewer's viewport is part of the Presenter View, as a rectangle in video coordinates, so a follower on a
  window of another shape sees the same region.

## Settings

One table declares every setting. A row states its scope, whether it is `secret` and whether it needs
`full_access`; `secret` and `full_access` default to no, and every row names its scope.

| Scope | Stored | Owner | Guest |
|-------|--------|-------|-------|
| Server | the server's settings file | reads and writes | never sees it |
| Personal | the server's settings file | reads and writes; the same on every device | gets the built-in default |
| Device | the device: the shell's own file in the desktop app, browser storage in a browser | reads and writes | reads and writes |

Choose the scope with the first question that answers yes:

1. Must everyone looking at the same Session have the same value to see the same thing? It is not a setting. It
   belongs to the Session: the Presenter View or the Workspace.
2. Does the right value depend on the screen in front of you? **Device** (theme, text size).
3. Does it change what the server connects to, stores or spends? **Server**.
4. Otherwise it is how the Owner likes to work: **Personal** (shortcuts, defaults for new overlays).

Rules:

- A setting has one home. No client keeps a second copy, and no value is merged from two places.
- The server never stores or receives a Device value. A Guest gets the built-in default of every Personal row and the
  standard shortcuts, and cannot change them.
- Shortcuts are Personal and saved in the same request as the rows, but they are not rows.
- A save names the keys it changes; the last write to a key wins. The server sends every Owner connection a
  `Changed` notice for the topic (settings, sessions, recents, plugins) with its new revision and no value; the
  client then fetches again. A client does not poll; it retries a failed read a bounded number of times. A Guest connection is told no topic: each topic's routes
  are the Owner's.
- A secret is write-only. A client learns whether it is set and, if it is a reference, its `$NAME`; the value
  never leaves the server.
- What can be chosen (render catalogs, plugins, decoders) lives with the server and is listed to whoever may
  choose. Which one is chosen is a setting with its own scope. The theme choices are a fixed list declared in the
  settings table row.
- `full_access` rows: the two folder settings and `server.password_full_access` itself.

## Native Features

Restart and opening the log folder exist only in a desktop window on the server's own machine; the frontend asks the
shell bridge and shows the control only then, never inferring it from the Role. File and folder dialogs have a
server-side stand-in: `usePickPath` uses the shell's dialog when offered, else the path picker, which an Owner uses
to browse the server through `GET /api/files/folder`.

## Adding Something

| Adding | Decide |
|--------|--------|
| A route | Public, Owner, Full access or Local, by the Full access test |
| A setting | One table row: scope by the four questions, `secret`, `full_access` |
| A Session field | Whether a Guest receives it; if so, it passes the projection rule |
| A native feature | The shell bridge offers it; what a remote Owner uses instead |
| Something to choose from | The list lives with the server; the choice is a setting |
