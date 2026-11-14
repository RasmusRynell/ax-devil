import { useState, type ReactNode } from "react";
import type { ItemState } from "../generated/api";
import type { Information } from "../live/information";
import { AddLiveStreamDialog } from "./AddLiveStreamDialog";
import { AddPlaylistDialog } from "./AddPlaylistDialog";
import { AddVideoDialog } from "./AddVideoDialog";
import { InformationDialog } from "./InformationDialog";

/** The form of `item`'s kind, filled in from its recipe; it replaces the item when saved. */
function EditDialog({ item, onClose }: { item: ItemState; onClose: () => void }) {
  const { recipe } = item;
  switch (recipe?.type) {
    case "Video":
      return <AddVideoDialog initial={recipe} editing={item.id} onClose={onClose} />;
    case "Playlist":
      return (
        <AddPlaylistDialog
          initial={{ resolver: recipe.resolver, label: recipe.label, settings: recipe.settings }}
          editing={item.id}
          onClose={onClose}
        />
      );
    case "Live":
      return <AddLiveStreamDialog initial={recipe} editing={item.id} onClose={onClose} />;
    case undefined:
      return null;
  }
}

/**
 * The dialogs a row of the sidebar opens on an item: its Edit form and its Information. Render `dialogs` beside the
 * rows and call `edit` or `inform` to open one.
 */
export function useItemDialogs(): {
  edit: (item: ItemState) => void;
  inform: (info: Information) => void;
  dialogs: ReactNode;
} {
  const [editing, setEditing] = useState<ItemState | null>(null);
  const [informing, setInforming] = useState<Information | null>(null);
  return {
    edit: setEditing,
    inform: setInforming,
    dialogs: (
      <>
        {editing && <EditDialog item={editing} onClose={() => setEditing(null)} />}
        {informing && <InformationDialog info={informing} onClose={() => setInforming(null)} />}
      </>
    ),
  };
}
