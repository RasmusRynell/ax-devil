// Generated from crates/api-types by make; do not edit.

import type { AppInfo, CheckFilesRequest, ChoiceInfo, CopiedCatalog, CopyCatalogRequest, DeleteCatalogRequest, ExpansionsRequest, FileCheck, FolderListing, FreshCatalog, HandlersInfo, InstallRequest, LiveCheckRequest, LiveExpansions, LiveResolutions, NetworkRow, PageError, PathExists, PathQuery, PluginsInfo, RecentWorkspace, ReloadCatalogRequest, RemoveRequest, ResolutionsRequest, ResolveRequest, ResolvedInfo, ScanRequest, ScanRequestWire, ScanStarted, SessionSummary, SettingsPatch, SettingsState, SourcesAnswer, SourcesRequest, UpdateRequest } from "./api";

/** Every JSON route by `"METHOD /path"`: what it takes (a GET's request is its query), what it answers (`null` for a 204) and its path parameters. */
export interface Routes {
  "GET /api/info": { request: null; response: AppInfo; params: null };
  "POST /api/client-errors": { request: PageError; response: null; params: null };
  "GET /api/settings": { request: null; response: SettingsState; params: null };
  "PATCH /api/settings": { request: SettingsPatch; response: SettingsState; params: null };
  "GET /api/handlers": { request: null; response: HandlersInfo; params: null };
  "GET /api/plugins": { request: null; response: PluginsInfo; params: null };
  "POST /api/plugins/install": { request: InstallRequest; response: PluginsInfo; params: null };
  "POST /api/plugins/remove": { request: RemoveRequest; response: PluginsInfo; params: null };
  "POST /api/plugins/update": { request: UpdateRequest; response: PluginsInfo; params: null };
  "POST /api/catalogs/copy": { request: CopyCatalogRequest; response: CopiedCatalog; params: null };
  "POST /api/catalogs/reload": { request: ReloadCatalogRequest; response: FreshCatalog; params: null };
  "POST /api/catalogs/delete": { request: DeleteCatalogRequest; response: null; params: null };
  "POST /api/resolvers/{id}/scan": { request: ScanRequest; response: Array<ChoiceInfo>; params: { id: string } };
  "POST /api/resolvers/{id}/resolve": { request: ResolveRequest; response: ResolvedInfo; params: { id: string } };
  "POST /api/live/resolutions": { request: ResolutionsRequest; response: LiveResolutions; params: null };
  "POST /api/live/sources": { request: SourcesRequest; response: SourcesAnswer; params: null };
  "POST /api/live/check": { request: LiveCheckRequest; response: NetworkRow | null; params: null };
  "POST /api/live/expansions": { request: ExpansionsRequest; response: LiveExpansions; params: null };
  "POST /api/files/check": { request: CheckFilesRequest; response: Array<FileCheck>; params: null };
  "GET /api/files/folder": { request: PathQuery; response: FolderListing; params: null };
  "GET /api/files/exists": { request: PathQuery; response: PathExists; params: null };
  "POST /api/network/scan": { request: ScanRequestWire; response: ScanStarted; params: null };
  "POST /api/network/cancel": { request: null; response: null; params: null };
  "GET /api/workspaces/recent": { request: null; response: Array<RecentWorkspace>; params: null };
  "GET /api/sessions": { request: null; response: Array<SessionSummary>; params: null };
}
