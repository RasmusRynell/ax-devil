/** A suggested name as one visible, portable filename component. */
export function safeFileName(name: string, suffix?: string): string;
/** A chosen path's folder (with its final separator) and filename. */
export function splitPath(path: string): { folder: string; name: string };
