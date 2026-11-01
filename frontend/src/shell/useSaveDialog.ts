import { useEffect, useRef, useState } from "react";
import type { ApiError, SettingsChange } from "../generated/api";
import { useSettings } from "../settings";

/**
 * What a dialog that stores settings needs: `submit` stores a change, closes the dialog when it is accepted, and
 * otherwise returns and keeps the refusal to show where it applies. `saving` is true meanwhile.
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
  const submit = async (change: SettingsChange): Promise<ApiError | null> => {
    setSaving(true);
    setRefusal(null);
    const refused = await save(change);
    // A dialog closed meanwhile has nothing left to update; the write itself still completed.
    if (!mounted.current) return null;
    setSaving(false);
    if (refused) setRefusal(refused);
    else onClose();
    return refused;
  };
  return { saving, refusal, submit };
}
