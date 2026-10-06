"""Identify the base app without importing Qt or depending on the working directory."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


@dataclass(frozen=True)
class Host:
    """App source and compatibility identity for one managed plugin installation."""

    location: Path
    version: str
    editable: bool = False
    # Direct URL of an app installed from a file or VCS instead of a package index.
    source: str = ""
    # Exact installed versions of the app's dependency closure; plugin environments may not change them.
    constraints: tuple[str, ...] = ()

    @property
    def requirement(self) -> str:
        """Keep released apps pinned even when plugin dependencies are upgraded."""
        if self.editable:
            return "ax-devil"
        if self.source.startswith("file:") and Path(unquote(urlparse(self.source).path)).is_dir():
            # A directory can change under an unchanged version, so plugins could run different app code.
            raise ValueError("Plugins need ax-devil installed editable, from a wheel, a Git URL, or a package index")
        return f"ax-devil @ {self.source}" if self.source else f"ax-devil=={self.version}"

    @property
    def fingerprint(self) -> str:
        """Detect app, interpreter, dependency, or editable project metadata changes at startup."""
        metadata = (self.location / "pyproject.toml").read_bytes() if self.editable else b""
        # uv may reach the same managed Python through a minor-version link or its patch directory.
        base_python = Path(sys.base_prefix).resolve()
        identity = f"{self.version}:{self.source}:{sys.version}:{base_python}:{sys.platform}"
        digest = hashlib.sha256(identity.encode())
        digest.update(metadata)
        digest.update("\n".join(self.constraints).encode())
        return digest.hexdigest()

    @property
    def command(self) -> str:
        """Return the normal invocation for this app installation."""
        return "uv run ax-devil" if self.editable else "ax-devil"


def _dependency_versions(app: importlib.metadata.Distribution) -> tuple[str, ...]:
    """Pin the installed versions of the app's dependency closure, ignoring unrelated or development packages."""
    versions: dict[str, str] = {}
    pending: list[tuple[importlib.metadata.Distribution, frozenset[str]]] = [(app, frozenset())]
    while pending:
        distribution, extras = pending.pop()
        for value in distribution.requires or ():
            requirement = Requirement(value)
            name = canonicalize_name(requirement.name)
            marker = requirement.marker
            if name in versions or not (
                marker is None or any(marker.evaluate({"extra": extra}) for extra in ("", *extras))
            ):
                continue
            try:
                dependency = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                continue
            versions[name] = dependency.version
            pending.append((dependency, frozenset(requirement.extras)))
    return tuple(f"{name}=={version}" for name, version in sorted(versions.items()))


def _direct_source(direct_url: dict[str, Any]) -> str:
    """Return the PEP 610 install URL, or an empty string for package index installs."""
    vcs = direct_url.get("vcs_info")
    subdirectory = f"#subdirectory={direct_url['subdirectory']}" if "subdirectory" in direct_url else ""
    if vcs is not None:
        return f"{vcs['vcs']}+{direct_url['url']}@{vcs['commit_id']}{subdirectory}"
    return f"{direct_url['url']}{subdirectory}" if "url" in direct_url else ""


def current_host() -> Host:
    """Distinguish editable source from installed distributions using package metadata."""
    distribution = importlib.metadata.distribution("ax-devil")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    constraints = _dependency_versions(distribution)
    if direct_url.get("dir_info", {}).get("editable", False):
        # Follow the actual imported code, never the caller's current directory.
        from .project import read_project

        for parent in Path(__file__).resolve().parents:
            if (parent / "pyproject.toml").is_file():
                project = read_project(parent).get("project", {})
                if project.get("name") == "ax-devil":
                    return Host(parent, project["version"], editable=True, constraints=constraints)
        raise ValueError("The editable ax-devil project is missing")
    # sys.prefix stays stable when uv replaces a tool environment during upgrade.
    return Host(
        Path(sys.prefix).absolute(), distribution.version, source=_direct_source(direct_url), constraints=constraints
    )
