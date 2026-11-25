from __future__ import annotations

from pathlib import Path

import pytest

from nebius_tf_cost.catalog import load_catalog
from nebius_tf_cost.config import EstimateConfig

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "prices.json"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def catalog():
    return load_catalog(CATALOG)


@pytest.fixture
def base_cfg(tmp_path: Path) -> EstimateConfig:
    # input_path validated later; point at a real file for construction
    plan = FIXTURES / "plan_create.json"
    return EstimateConfig(input_path=plan, catalog_path=CATALOG, period="month", currency="USD")
