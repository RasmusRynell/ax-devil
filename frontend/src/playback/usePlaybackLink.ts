import { useCallback } from "react";
import type { ContentRef } from "../generated/api";
import { useStream } from "../session/session";
import type { Connect } from "../stream/wire";

/** One seat's playback admission: successors wait for retiring sockets to acknowledge release. */
export function usePlaybackLink(key: ContentRef, connect: Connect) {
  const { ticket, release } = useStream(key);
  const tracked = useCallback<Connect>(
    (incoming) => {
      const connection = connect(incoming);
      return {
        send: (message) => connection.send(message),
        close: () => {
          connection.close();
          release(connection.closed ?? Promise.resolve());
        },
      };
    },
    [connect, release],
  );
  return { ticket, connect: tracked };
}
