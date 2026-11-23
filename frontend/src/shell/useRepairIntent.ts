import { useRef, useState } from "react";

/** Explicit choices that repair recovered raw values, alongside #390's saved/displayed comparison. */
export function useRepairIntent<K extends string>(recovered: readonly K[] = []) {
  const [repairable] = useState(() => new Set(recovered));
  const edited = useRef(new Set<K>());
  return {
    repairable,
    edit: (key: K) => {
      edited.current.add(key);
    },
    keys: () => [...edited.current].filter((key) => repairable.has(key)),
  };
}
