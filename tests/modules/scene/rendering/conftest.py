"""Fixtures shared by the catalog manager tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.catalog_helpers import GatedCatalogLoader


@pytest.fixture
def gated_loader() -> Iterator[GatedCatalogLoader]:
    """A loader whose held reads are released after the test, so no worker outlives it."""
    loader = GatedCatalogLoader()
    yield loader
    loader.release_all()
