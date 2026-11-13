//! The JSON routes the frontend calls, declared once: method, path, request and response. `src/routes/mod.rs` mounts
//! each row's method and path, and `export-types` writes the table as `frontend/src/generated/routes.ts` for `ask`.
//! The bodies are declared here, not derived from the handlers, which must take and return exactly these.
//! A GET's request is its query string; `()` and `Option<T>` answer a 204 as `null`.

use crate::{
    AppInfo, SessionSummary, SettingsPatch, SettingsState,
    catalogs::{
        CopiedCatalog, CopyCatalogRequest, DeleteCatalogRequest, FreshCatalog, ReloadCatalogRequest,
    },
    handlers::{
        HandlersInfo, InstallRequest, PluginsInfo, RemoveRequest, ResolveRequest, ResolvedInfo,
        ScanRequest, UpdateRequest,
    },
    live::{
        ExpansionsRequest, LiveCheckRequest, LiveExpansions, LiveResolutions, NetworkRow,
        ResolutionsRequest, ScanRequestWire, ScanStarted, SourcesAnswer, SourcesRequest,
    },
    report::PageError,
    workspace::{
        CheckFilesRequest, FileCheck, FolderListing, PathExists, PathQuery, RecentWorkspace,
    },
};
use handler_api::Choice;
use ts_rs::{Dependency, TS, TypeVisitor};

/// An HTTP method of a JSON route.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Method {
    /// `GET`.
    Get,
    /// `POST`.
    Post,
    /// `PATCH`.
    Patch,
}

impl Method {
    /// The method as HTTP spells it.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Get => "GET",
            Self::Post => "POST",
            Self::Patch => "PATCH",
        }
    }
}

const GET: Method = Method::Get;
const POST: Method = Method::Post;
const PATCH: Method = Method::Patch;

/// One JSON route; every row of the table below is a unit type implementing it.
pub trait Endpoint {
    /// The route's method.
    const METHOD: Method;
    /// The route's path, with `{name}` for each path parameter.
    const PATH: &'static str;
    /// What the caller sends: the JSON body, or for a GET the query string.
    type Request: TS + 'static;
    /// What the route answers.
    type Response: TS + 'static;
}

/// A row of [`ENDPOINTS`], for the exporter.
pub struct Row {
    /// The route's method.
    pub method: Method,
    /// The route's path.
    pub path: &'static str,
    /// The request's TypeScript type.
    pub request: fn() -> String,
    /// The response's TypeScript type.
    pub response: fn() -> String,
}

/// Visits `T` and the types it is made of (the element of a `Vec`, the inner type of an `Option`) that are exported.
fn visit_named<T: TS + 'static>(types: &mut impl TypeVisitor) {
    struct Named<'a, V>(&'a mut V);
    impl<V: TypeVisitor> TypeVisitor for Named<'_, V> {
        fn visit<U: TS + 'static + ?Sized>(&mut self) {
            if Dependency::from_ty::<U>().is_some() {
                self.0.visit::<U>();
            }
        }
    }
    let mut named = Named(types);
    named.visit::<T>();
    T::visit_generics(&mut named);
}

macro_rules! endpoints {
    ($($name:ident: $method:ident $path:literal, $request:ty => $response:ty;)*) => {
        $(
            #[doc = concat!("`", stringify!($method), " ", $path, "`")]
            pub struct $name;

            impl Endpoint for $name {
                const METHOD: Method = $method;
                const PATH: &'static str = $path;
                type Request = $request;
                type Response = $response;
            }
        )*

        /// Every JSON route, in the order the table lists them.
        pub const ENDPOINTS: &[Row] = &[$(Row {
            method: $method,
            path: $path,
            request: <$request as TS>::name,
            response: <$response as TS>::name,
        }),*];

        /// Visits the exported types the table's requests and responses name.
        pub fn visit_types(types: &mut impl TypeVisitor) {
            $(visit_named::<$request>(types); visit_named::<$response>(types);)*
        }
    };
}

endpoints! {
    Info: GET "/api/info", () => AppInfo;
    ClientErrors: POST "/api/client-errors", PageError => ();
    GetSettings: GET "/api/settings", () => SettingsState;
    PatchSettings: PATCH "/api/settings", SettingsPatch => SettingsState;
    GetHandlers: GET "/api/handlers", () => HandlersInfo;
    GetPlugins: GET "/api/plugins", () => PluginsInfo;
    InstallPlugin: POST "/api/plugins/install", InstallRequest => PluginsInfo;
    RemovePlugin: POST "/api/plugins/remove", RemoveRequest => PluginsInfo;
    UpdatePlugin: POST "/api/plugins/update", UpdateRequest => PluginsInfo;
    CopyCatalog: POST "/api/catalogs/copy", CopyCatalogRequest => CopiedCatalog;
    ReloadCatalog: POST "/api/catalogs/reload", ReloadCatalogRequest => FreshCatalog;
    DeleteCatalog: POST "/api/catalogs/delete", DeleteCatalogRequest => ();
    ScanResolver: POST "/api/resolvers/{id}/scan", ScanRequest => Vec<Choice>;
    ResolveResolver: POST "/api/resolvers/{id}/resolve", ResolveRequest => ResolvedInfo;
    ListResolutions: POST "/api/live/resolutions", ResolutionsRequest => LiveResolutions;
    ListSources: POST "/api/live/sources", SourcesRequest => SourcesAnswer;
    CheckLive: POST "/api/live/check", LiveCheckRequest => Option<NetworkRow>;
    ExpandLive: POST "/api/live/expansions", ExpansionsRequest => LiveExpansions;
    CheckFiles: POST "/api/files/check", CheckFilesRequest => Vec<FileCheck>;
    ListFolder: GET "/api/files/folder", PathQuery => FolderListing;
    FileExists: GET "/api/files/exists", PathQuery => PathExists;
    ScanNetwork: POST "/api/network/scan", ScanRequestWire => ScanStarted;
    CancelNetwork: POST "/api/network/cancel", () => ();
    RecentWorkspaces: GET "/api/workspaces/recent", () => Vec<RecentWorkspace>;
    ListSessions: GET "/api/sessions", () => Vec<SessionSummary>;
}
