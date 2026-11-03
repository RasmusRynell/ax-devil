import type { ErrorCode, ErrorWire, RefusalCode } from "./generated/api";

/**
 * What each public failure code says to someone who is not told the backend's own wording: the Session socket and
 * the playback socket send only the code to a watcher, so this window words it.
 */
export const ERRORS: Record<ErrorCode, string> = {
  InvalidPath: "That path is not valid.",
  FileNotFound: "The file was not found.",
  FileUnreadable: "The file cannot be read.",
  NotAVideo: "The file is not a video.",
  UnsupportedFormat: "The video format is not supported.",
  IndexingFailed: "The video could not be indexed.",
  FileChanged: "The file changed while it was open.",
  DecodeFailed: "A frame could not be decoded.",
  ContentNotFound: "The video is no longer available.",
  ProtocolViolation: "The backend and this window disagree on the protocol.",
  VideoClosed: "The video was closed.",
  UnknownHandler: "No handler is installed for that.",
  OverlayInvalid: "The overlay file could not be read.",
  PlaylistInvalid: "The playlist could not be loaded.",
  PluginInvalid: "The plugin cannot be used.",
  CatalogInvalid: "The render catalog is not valid.",
  WorkspaceInvalid: "The workspace file is not valid.",
  WorkspaceInUse: "The workspace is open in another window.",
  SaveFailed: "The workspace could not be saved.",
  Internal: "The backend failed.",
};

/** What a refused command says. */
export const REFUSALS: Record<RefusalCode, string> = {
  SeatGone: "This window is no longer in the session.",
  NotAllowed: "Your role in this session cannot do that.",
  NotSharing: "The session is not shared.",
  AlreadySharing: "The session is already shared.",
  ContentUnavailable: "That video is not open.",
  TooManyStreams: "Too many videos are open.",
  BadTicket: "The backend refused to open that video.",
  Link: "That link is no longer valid.",
  Full: "That session is full.",
  Invalid: "The backend cannot open that.",
};

/** A failure's own reason when the audience may read it, else the words for its code. */
export function reasonOf(error: { code: ErrorCode; reason?: string }): string {
  return error.reason ?? ERRORS[error.code];
}

/** A command or a ticket request the backend refused; nothing changed. */
export class CommandRefused extends Error {
  readonly code: RefusalCode;
  constructor(code: RefusalCode) {
    super(REFUSALS[code]);
    this.name = "CommandRefused";
    this.code = code;
  }
}

/** A command the backend accepted but could not carry out; nothing changed. */
export class CommandFailed extends Error {
  readonly error: ErrorWire;
  constructor(error: ErrorWire) {
    super(reasonOf(error));
    this.name = "CommandFailed";
    this.error = error;
  }
}
