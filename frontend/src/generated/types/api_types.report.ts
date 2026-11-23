// Generated from api_types::report by make; do not edit.

/**
 * An error nobody caught in a page: the body of `POST /api/client-errors`. The backend adds who sent it.
 */
export type PageError = { 
/**
 * `Name: message`.
 */
summary: string, 
/**
 * The summary and the stack trace.
 */
details: string, 
/**
 * The page's address without its query and fragment, which may hold credentials.
 */
page: string, 
/**
 * Whether the page runs in the desktop app (and not in a browser).
 */
desktop: boolean, };
