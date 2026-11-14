import { createContext, createElement, useContext, useMemo } from "react";
import type { ReactNode } from "react";
import { useAccess } from "../client/access";
import { useResource, type Resource } from "../client/resource";
import type { CommandInfo, LaneReaderInfo, PluginsInfo, ViewInfo } from "../generated/api";

/** A plugin view with what its bus may reach: the plugin's own lane readers and commands. */
export interface PluginView {
  plugin: string;
  view: ViewInfo;
  readers: readonly LaneReaderInfo[];
  commands: readonly CommandInfo[];
}

/** What a plugin view is called across the app: `<plugin id>.<view id>`, which is also its `SidebarView::Plugin` id. */
export const viewName = ({ plugin, view }: { plugin: string; view: { id: string } }): string =>
  `${plugin}.${view.id}`;

/** A plugin command with the plugin it belongs to and what it runs: a lane reader, or a view. */
export type PluginCommand = CommandInfo & {
  plugin: string;
  reader?: LaneReaderInfo;
  view?: ViewInfo;
};

interface Listed {
  views: readonly PluginView[];
  commands: readonly PluginCommand[];
  /** How many plugins failed to load. */
  failed: number;
}

/** The views and commands of the plugins that loaded, and how many did not; an answer that is no listing has none. */
const listedOf = (info: PluginsInfo | undefined): Listed => {
  const plugins = Array.isArray(info?.plugins) ? info.plugins : [];
  const loaded = plugins.flatMap(({ status }) => (status.state === "loaded" ? [status] : []));
  return {
    views: loaded.flatMap((status) =>
      status.views.map((view) => ({
        plugin: status.id,
        view,
        readers: status.readers,
        commands: status.commands,
      })),
    ),
    commands: loaded.flatMap((status) =>
      status.commands.map((command) => ({
        ...command,
        plugin: status.id,
        reader: status.readers.find(({ id }) => id === command.runs),
        view: status.views.find(({ id }) => id === command.runs),
      })),
    ),
    failed: plugins.length - loaded.length,
  };
};

/** The one reading of `/api/plugins`: the listing as sent, with `apply` and `reload` for what changes it, and what it offers. */
export type Listing = Resource<PluginsInfo> & Listed;

const NOTHING: Listing = {
  ...listedOf(undefined),
  value: undefined,
  error: undefined,
  apply: () => {},
  reload: () => {},
};
const ListingContext = createContext<Listing>(NOTHING);

/**
 * Reads the plugin listing for what is below it. Only an Owner reads it, so a Guest's window has none; a page without
 * this provider has none either.
 */
export function PluginViewsProvider({ children }: { children: ReactNode }) {
  const { owner } = useAccess();
  const { value, error, apply, reload } = useResource<PluginsInfo>(
    "plugins",
    owner ? "/api/plugins" : null,
  );
  const listing = useMemo(
    () => ({ value, error, apply, reload, ...listedOf(value) }),
    [value, error, apply, reload],
  );
  return createElement(ListingContext, { value: listing }, children);
}

export function usePluginListing(): Listing {
  return useContext(ListingContext);
}

export function usePluginViews(place: ViewInfo["place"]): readonly PluginView[] {
  const { views } = usePluginListing();
  return useMemo(() => views.filter(({ view }) => view.place === place), [views, place]);
}

export function usePluginCommands(): readonly PluginCommand[] {
  return usePluginListing().commands;
}
