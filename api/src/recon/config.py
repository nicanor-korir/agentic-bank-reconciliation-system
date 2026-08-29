"""Frozen run configuration.

Everything a matching decision depends on lives here and is snapshotted onto
`runs.config_snapshot`. If a threshold is not in this object, replay cannot
prove what it was -- so put it here, not in a function default.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MatchConfig(BaseModel):
    """Immutable matching parameters. Snapshotted per run."""

    model_config = {"frozen": True}

    tier0_date_window_days: int = 2
    tier1_date_window_days: int = 7
    tier1_confidence: float = 0.95

    # Tier 2 retrieval
    candidate_limit: int = 10
    amount_tolerance_bps: int = 50  # 0.50%, covers FX and rounding drift
    amount_tolerance_floor_minor: int = 100  # never tighter than 1.00 USD
    tier2_date_window_days: int = 30
    subset_max_items: int = 4  # bounded subset-sum for split/batch settlement
    subset_max_pool: int = 60

    # Tier 3 adjudication
    tier3_autocommit_confidence: float = 0.90
    tier3_concurrency: int = 8
    run_cost_ceiling_micro: int = 2_000_000  # $2.00, halts the graph (NOTES.md 0.5.5)

    model_version: str = "claude-sonnet-5"


class Settings(BaseSettings):
    """Environment-derived settings. Never part of a matching decision."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str = "postgresql://recon:recon@localhost:55432/recon"
    weaviate_url: str = "http://localhost:58080"
    recon_tenant: str = "harborview"
    recon_seed: int = 20260601
    recon_data_dir: str = "./data"
    anthropic_api_key: str = ""

    match: MatchConfig = Field(default_factory=MatchConfig)


def get_settings() -> Settings:
    return Settings()
