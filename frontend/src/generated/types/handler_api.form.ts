// Generated from handler_api::form by make; do not edit.

/**
 * One setting.
 */
export type Field = { 
/**
 * The key in the settings.
 */
key: string, 
/**
 * What the user reads.
 */
label: string, 
/**
 * What kind of value the setting holds.
 */
kind: FieldKind, 
/**
 * Whether the handler fails without a value (after the default is filled in).
 */
required: boolean, 
/**
 * The value used when none is given; absent when there is none.
 */
default?: unknown, };

/**
 * What kind of value a setting holds, and so which control it gets.
 */
export type FieldKind = { "kind": "Folder" } | { "kind": "File", 
/**
 * The extensions the file may have.
 */
extensions: Array<string>, } | { "kind": "Text" } | { "kind": "Integer", 
/**
 * The smallest value allowed.
 */
min: bigint, } | { "kind": "Handler" } | { "kind": "DecoderOptions", 
/**
 * The key of the `Handler` field whose decoder's options these are.
 */
handler_field: string, } | { "kind": "Scan", 
/**
 * The settings the scan reads, whose change invalidates its result.
 */
depends_on: Array<string>, 
/**
 * What a count-only scan tells the user; `None` for a scan whose values are picked.
 */
count?: ScanCount, };

/**
 * The sentences of a count-only scan, which are all the user sees of what it found.
 */
export type ScanCount = { 
/**
 * Shown while a setting the scan reads is empty.
 */
missing: string, 
/**
 * Shown when the scan found something; `{count}` stands for how many.
 */
found: string, 
/**
 * Shown when the scan found nothing.
 */
none: string, };
