import { useState } from "react";
import { scopeOf } from "../client/commands";
import { RowList } from "../kit/RowList";
import { useHandlers } from "../resolvers/useHandlers";
import { useAllowed } from "../session/session";
import { AddPlaylistDialog } from "./AddPlaylistDialog";

const VIDEO = "add-video";
const RESOLVER = "resolver:";

/**
 * The Offline view's Add list under its sections: a single video, then a row for each playlist resolver the backend
 * offers, in its order, whose hover is the resolver's description and, for an installed plugin's, `From <plugin name>.`.
 * The video row asks the window (`onRun`) for the Add
 * Video dialog; a resolver's row opens Add Playlist with that resolver chosen. The resolvers are fetched whenever the
 * view is shown. The list takes its height, shrinks to one row when the window is short, and then scrolls.
 */
export function OfflineAdd({ onRun }: { onRun: (id: string) => void }) {
  const allowed = useAllowed();
  const { handlers } = useHandlers();
  const [resolver, setResolver] = useState<string | null>(null);
  const may = allowed[scopeOf("add-playlist")];
  return (
    <>
      <div className="flex min-h-row shrink flex-col overflow-y-auto">
        <RowList
          heading="Add"
          rows={[
            {
              id: VIDEO,
              label: "Single video",
              icon: "square-play",
              title: "Add a video file",
              disabled: !allowed[scopeOf(VIDEO)],
            },
            ...(handlers?.resolvers ?? []).map(({ id, display_name, description, plugin }) => ({
              id: `${RESOLVER}${id}`,
              label: display_name,
              icon: "list-video" as const,
              title: [
                description.split(/\s+/).filter(Boolean).join(" "),
                plugin && `From ${plugin}.`,
              ]
                .filter(Boolean)
                .join("\n"),
              disabled: !may,
            })),
          ]}
          onSelect={(id) => (id === VIDEO ? onRun(VIDEO) : setResolver(id.slice(RESOLVER.length)))}
        />
      </div>
      {resolver !== null && (
        <AddPlaylistDialog initial={{ resolver }} onClose={() => setResolver(null)} />
      )}
    </>
  );
}
