import { useEffect, useRef, useState } from "react";
import type { ApiError, SettingsPatch } from "../generated/api";
import { isEmpty, useSettings } from "../settings";

/** How a `submit` ended: stored, refused (kept in `refusal` to show where it applies), or the dialog was closed first. */
export type SaveOutcome =
  | { kind: "saved" }
  | { kind: "closed" }
  | { kind: "refused"; refusal: ApiError };

/**
 * What a dialog that stores settings needs: `submit` stores a patch of what the dialog changed, closes the dialog when
 * it is accepted, and returns how it ended; a patch that names nothing is not sent. `saving` is true meanwhile.
 */
export function useSaveDialog(onClose: () => void) {
  const { apply } = useSettings();
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<ApiError | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const submit = async (patch: SettingsPatch): Promise<SaveOutcome> => {
    if (isEmpty(patch)) {
      onClose();
      return { kind: "saved" };
    }
    setSaving(true);
    setRefusal(null);
    const refused = await apply(patch);
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
