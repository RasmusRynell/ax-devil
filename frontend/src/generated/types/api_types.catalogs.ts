// Generated from api_types::catalogs by make; do not edit.

/**
 * One catalog the picker offers.
 */
export type CatalogInfoWire = { 
/**
 * The id, such as `built-in:standard`.
 */
id: string, 
/**
 * What the user reads.
 */
name: string, 
/**
 * What the catalog is for.
 */
description: string, 
/**
 * Why the catalog cannot be used; absent for a usable catalog.
 */
problem?: string, };

/**
 * The reply to a copy: the id of the new catalog.
 */
export type CopiedCatalog = { 
/**
 * The id of the new user catalog, `user:<file>`.
 */
id: string, };

/**
 * A request to copy a catalog under a new name.
 */
export type CopyCatalogRequest = { 
/**
 * The id of the catalog to copy: `built-in:<name>` or `user:<file>`.
 */
source: string, 
/**
 * The revision of the source the viewer compiled; a source that changed since is refused.
 */
revision: number, 
/**
 * What the user named the copy.
 */
name: string, };

/**
 * A request to delete a user catalog.
 */
export type DeleteCatalogRequest = { 
/**
 * The id of the user catalog to delete, `user:<file>`.
 */
id: string, };

/**
 * A request to read a catalog file again.
 */
export type ReloadCatalogRequest = { 
/**
 * The id of the catalog to read again: `built-in:<name>` or `user:<file>`.
 */
id: string, };
