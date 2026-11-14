# Views, commands and the bus

A **view** is a page the plugin ships, shown in a sandboxed frame inside ax-devil. A **command** is a menu entry and
rebindable key that runs one of the plugin's own handlers. A plugin of only views and commands needs no `program` or
`code`. Manifest fields beside `decoder` and `task` are in [reference.md](reference.md).

```json
{"kind": "view", "id": "review", "title": "Label review", "icon": "images", "place": "video", "document": "views/review/index.html"}
{"kind": "command", "id": "export_coco", "title": "Export COCO", "on": "lane", "shortcut": "Ctrl+Alt+E", "runs": "coco"}
```

- `place` is `sidebar` (an activity bar button) or `video` (a layer over one lane's viewer, switched on in the lane
  menu; it ignores the pointer until `viewer.set {"capture": true}`, and Escape releases).
- `document` is a file of the plugin's `views/` folder; only that folder is served. `icon` is optional.
- `on` is `app`, `item` or `lane`: where the command appears. `runs` names an own `decoder` with `input: "lanes"`, a
  `task` or a `view`; a view receives the `command` event. Running a decoder or task opens a dialog to pick lanes and
  options. A `shortcut` that is reserved or already taken ships unbound.
- A `view` or `command` whose `document` or `runs` does not resolve is skipped with the reason, like an unknown kind.
- A view or command id need only be unique within its plugin.

## The frame

The page loads from its own address, `<script>` and `<style>` inline or from its own files, and nothing else: no
network, no other site, no forms, no storage or cookies (the frame has an opaque origin). Keep state in a lane's options.
Navigating the frame closes it ("view left its page"). Nothing but the bus reaches ax-devil.

## The bus

On its first load the host posts one `MessagePort`; everything below travels over it as JSON messages, each with a
`type`.

- The view sends `{"type": "call", "id", "call", "epoch", "args"}`; `id` is a number it picks. The reply is
  `{"type": "reply", "id", "ok": true, "value"}` or `{"type": "reply", "id", "ok": false, "error", "reason"}`, where
  `error` is `stale`, `no_viewer`, `refused`, `too_large`, `invalid`, `unknown_call`, `cancelled` or `failed`.
- The view sends `{"type": "key", "key", "epoch"}` for a key it did not handle, with no reply; a key of an older `epoch` is dropped.
- The host sends the events below, each `{"type": ...}`.
- Every call but `cancel` carries the `epoch` of the last `context` event. The epoch changes when the view is bound to
  another viewer, item or entry (not when only the lanes, selection, theme or video rectangle change), and a call with
  another epoch, or before any `context`, is answered `stale` before anything happens: ask again with the new one.
  An effect names its item, entry and slot, so it cannot land on a later binding.
- No call takes a path: `lanes.set` and `task.run` refuse (`refused`) an option the operation's form declares as `file`
  or `folder`, and options over 256 KiB (`lanes.set`) or 8 MiB (`task.run`) (`too_large`).
- A call reaches only the plugin's own handlers: `runs` must be a `decoder` (`lanes.set`) or `task` (`task.run`) of
  this plugin, and `lanes.set` takes only a `slot` named `<plugin id>.<name>`, otherwise `refused`.

| Call | Does |
|------|------|
| `viewer.set {frame?, playing?, select?, capture?}` | Moves the bound viewer: `frame` a whole number, `select` `{lane, entity}` (a slot) or `null`; `no_viewer` when none is bound or it no longer shows the item and entry |
| `scenes.read {lane, from, count}` | The lane `lane` (any slot of the entry) as a lane file text, `count` samples from sample number `from`; the backend caps `count` at 2,000 and fails (`failed`) an answer over 8 MiB |
| `lanes.set {slot, runs, lanes, options, label}` or `{slot, remove: true}` | Adds, changes or removes the derived lane `<plugin id>.<name>`; `runs` is one of the plugin's `decoder` ids and `lanes` maps its keys to slots. A changed option derives again, and a result already built is reused |
| `task.run {runs, lanes, options}` | Runs one of the plugin's own tasks; `progress` events carry the call's id and the reply comes at the end |
| `cancel {id}` | Aborts that outstanding call of this view, which then fails `cancelled`; always answered `ok`, whatever the epoch |

| Event | Content |
|-------|---------|
| `context` | `epoch`, `item`, `entry` (null while no viewer is bound), `lanes` as `[{slot, label, basis, complete}]`, `selection` (`{lane, entity}` or null), `theme` (`light` or `dark`), `video` (`{x, y, width, height}` in CSS pixels of the page; only for a `video` view) |
| `playhead` | `frame`, `time` (microseconds or null), `playing`; at most every 33 ms, only the latest |
| `command` | `id`: a `command` whose `runs` is this view |
| `progress` | `id` of the `task.run`, with `done`, `total`, `text` as the task gave them |

`key` runs a command only if it is a built-in command of scope `view` or one of the plugin's own. A plugin command runs
on the viewer the view is bound to; a built-in one runs only while that viewer is in front.

A view reads what it needs with `scenes.read` and plots `values`, notes and events itself. It is not a place for secrets:
it gets the whole bus and what it reads can be passed to a task ([trust](../../../docs/plugins.md#trust)).

## Helper

Copy into the view as a module. It waits for the first `context`; `call` resolves with the reply's `value`, rejects
with an `Error` whose `code` is the reply's `error`, and carries the call's `id` for `call("cancel", {id})`.

```js
let port, epoch = 0, next = 0;
const pending = new Map(), handlers = new Map();
const ready = new Promise((resolve) => addEventListener("message", (e) => {
  port = e.ports[0];
  port.onmessage = ({ data }) => {
    if (data.type === "context") { epoch = data.epoch; resolve(); }
    if (data.type === "progress") pending.get(data.id)?.progress?.(data);
    if (data.type === "reply") {
      const p = pending.get(data.id);
      pending.delete(data.id);
      if (data.ok) p?.ok(data.value);
      else p?.fail(Object.assign(new Error(data.reason ?? data.error), { code: data.error }));
    }
    for (const fn of handlers.get(data.type) ?? []) fn(data);
  };
}, { once: true }));
export const on = (type, fn) => handlers.set(type, [...(handlers.get(type) ?? []), fn]);
export const key = (k) => port?.postMessage({ type: "key", key: k, epoch });
export const call = (name, args = {}, progress) => {
  const id = ++next;
  const reply = ready.then(() => new Promise((ok, fail) => {
    pending.set(id, { ok, fail, progress });
    port.postMessage({ type: "call", id, call: name, epoch, args });
  }));
  return Object.assign(reply, { id });
};
```

A `stale` reply means a newer `context` has arrived: ask again.
