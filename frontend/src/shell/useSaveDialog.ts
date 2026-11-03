import { useEffect, useRef, useState } from "react";
import type { ApiError, SettingsChange } from "../generated/api";
import { useSettings } from "../settings";

/** How a `submit` ended: stored, refused (kept in `refusal` to show where it applies), or the dialog was closed first. */
export type SaveOutcome =
  | { kind: "saved" }
  | { kind: "closed" }
  | { kind: "refused"; refusal: ApiError };

/**
 * What a dialog that stores settings needs: `submit` stores a change, closes the dialog when it is accepted, and
 * returns how it ended. `saving` is true meanwhile.
 */
export function useSaveDialog(onClose: () => void) {
  const { save } = useSettings();
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<ApiError | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const submit = async (change: SettingsChange): Promise<SaveOutcome> => {
    setSaving(true);
    setRefusal(null);
    const refused = await save(change);
    // A dialog closed meanwhile has nothing left to update; whether the write succeeded is no longer its to act on.
    if (!mounted.current) return { kind: "closed" };
    setSaving(false);
    if (refused) {
      setRefusal(refused);
      return { kind: "refused", refusal: refused };
    }
    onClose();
    return { kind: "saved" };
  };
  return { saving, refusal, submit };
}
