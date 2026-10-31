import { useEffect, useState } from "react";
import { COMMANDS } from "../commands";
import type { MenuCommand } from "../kit/Menu";
import { matchesShortcut } from "../kit/shortcut";
import { ActivityBar } from "./ActivityBar";
import { TitleBar } from "./TitleBar";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { Welcome } from "./Welcome";
import type { ActivityItem, ViewId } from "./views";

/** The application layout: menu bar over the activity bar, sidebar and central area. */
export function Shell() {
  const [view, setView] = useState<ViewId>("workspace");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());

  // Commands with behavior; every other command is a placeholder that does nothing yet.
  const actions: Record<string, () => void> = {
    sidebar: () => setSidebarOpen((open) => !open),
    settings: () => setSettingsOpen(true),
  };
  const run = (id: string) => actions[id]?.();
  const select = (item: MenuCommand) => {
    if (item.kind === "check")
      setChecked((now) => {
        const next = new Set(now);
        if (!next.delete(item.id)) next.add(item.id);
        return next;
      });
    else run(item.id);
  };
  const activate = (item: ActivityItem) => {
    if (item.opens === "command") run(item.id);
    else if (sidebarOpen && view === item.id) setSidebarOpen(false);
    else {
      setView(item.id as ViewId);
      setSidebarOpen(true);
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const match = [...COMMANDS.values()].find(
        (item) => item.shortcut && item.id in actions && matchesShortcut(event, item.shortcut),
      );
      if (match) {
        event.preventDefault();
        run(match.id);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  return (
    <div className="flex h-screen flex-col">
      <TitleBar checked={checked} onSelect={select} />
      <div className="flex min-h-0 flex-1">
        <ActivityBar active={sidebarOpen ? view : null} onSelect={activate} />
        {sidebarOpen && <Sidebar view={view} onRun={run} />}
        <main className="flex min-w-0 flex-1 overflow-auto">
          <h1 className="sr-only">ax-devil</h1>
          <Welcome onRun={run} />
        </main>
      </div>
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
