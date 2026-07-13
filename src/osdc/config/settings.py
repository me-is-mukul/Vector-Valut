"""Typed settings, backed by ``settings.toml`` with environment-variable overrides.

Precedence (highest first): env vars (``OSDC_*``) → ``settings.toml`` → defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import tomli_w
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from osdc.config import paths
from osdc.domain.enums import FileAction


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OSDC_",
        toml_file=paths.config_file(),
        extra="ignore",
    )

    # --- Onboarding -------------------------------------------------------
    onboarded: bool = False
    library_root: Path = Field(default_factory=paths.default_library_root)

    # ``NoDecode`` is load-bearing: without it pydantic-settings tries to JSON-parse
    # any env var destined for a complex type, so OSDC_WATCHED_FOLDERS="C:\Users\..."
    # dies as invalid JSON before the validator below ever runs. NoDecode hands us the
    # raw string instead.
    watched_folders: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    # --- Filing behaviour -------------------------------------------------
    # Default to COPY. Move is opt-in, after the user trusts the classifier
    # (planning.md §11, roadmap.md §2.6).
    file_action: FileAction = FileAction.COPY
    auto_approve: bool = True
    academic_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    general_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    current_semester: int | None = None
    undo_retention_days: int = 30

    # --- Ingestion --------------------------------------------------------
    # A single browser download emits several events; a file still being written
    # hashes to garbage. These two knobs are what stop both (roadmap.md §4 step 5).
    debounce_seconds: float = Field(default=1.0, gt=0)
    stability_poll_seconds: float = Field(default=0.4, gt=0)
    stability_checks: int = Field(default=2, ge=1)
    stability_timeout_seconds: float = Field(default=120.0, gt=0)
    max_file_size_mb: int = Field(default=200, gt=0)
    scan_on_startup: bool = True

    # --- Pipeline ---------------------------------------------------------
    worker_count: int = Field(default=2, ge=1)
    ocr_enabled: bool = True  # Phase 2 honours this; the NullOcr stub ignores it.

    # --- Models (Phase 3 / 5) ---------------------------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    #: Falls back to the no-model HashEmbedder when False — useful for fast tests and for
    #: running without the [ml] extra installed.
    use_real_embeddings: bool = True

    llm_model: str = "qwen2.5:7b"
    llm_host: str = "http://127.0.0.1:11434"

    #: CLIP, for natural-language image search. Images are indexed in place and never moved.
    clip_model: str = "clip-ViT-B-32"
    image_folders: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    image_search_floor: float = Field(default=0.20, ge=0.0, le=1.0)

    # --- Similarity floors ------------------------------------------------
    # Sentence embeddings have a high baseline: two unrelated texts still score ~0.4-0.55
    # with bge. These are the "is this even related?" gates, and they are separate from the
    # confidence thresholds above, which answer "which one, and how sure?".
    #
    # MEASURED, not guessed — `python scripts/calibrate.py` against the seed curriculum:
    #
    #   academic: real coursework scores >= 0.694 against its subject;
    #             non-academic docs peak at 0.549 against ANY subject.  floor -> 0.62
    #   general:  real general docs score >= 0.570 against their category;
    #             pure noise peaks at 0.492.                            floor -> 0.53
    #
    # The first guess at these was 0.45/0.40, which would have filed an article about
    # Arctic terns under Data Structures (0.549) and an invoice under Programming
    # Fundamentals (0.463). Hence the script.
    #
    # Calibrated on a handful of probes, NOT the labeled corpus roadmap.md §6.1 asks for.
    # Re-run the script once you have real documents.
    min_academic_similarity: float = Field(default=0.62, ge=0.0, le=1.0)
    min_general_similarity: float = Field(default=0.53, ge=0.0, le=1.0)
    softmax_temperature: float = Field(default=0.05, gt=0.0)

    #: Retrieved chunks below this are not evidence, and the RAG service refuses to answer
    #: rather than let the LLM improvise. See services/rag.py — this is the single setting
    #: standing between "grounded assistant" and "confidently invents your medical history".
    rag_relevance_floor: float = Field(default=0.55, ge=0.0, le=1.0)
    rag_top_k: int = Field(default=6, ge=1)

    # --- UI / desktop ------------------------------------------------------
    ui_native: bool = True  # a real OS window; needs the [desktop] extra (pywebview)
    ui_port: int = 8080
    ui_dark: bool = True
    #: Closing the window hides it to the tray instead of quitting, so the watcher keeps
    #: filing downloads. Quit properly from the tray menu.
    close_to_tray: bool = True

    @field_validator("watched_folders", "image_folders", mode="before")
    @classmethod
    def _coerce_folders(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [p for p in (s.strip() for s in v.split(";")) if p]
        return v

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=paths.config_file()),
        )


def load_settings() -> Settings:
    return Settings()


def save_settings(settings: Settings) -> None:
    """Persist to ``settings.toml``. Paths are written as strings; TOML has no Path."""
    paths.ensure_dirs()
    raw = settings.model_dump(mode="json", exclude_none=True)
    with paths.config_file().open("wb") as fh:
        tomli_w.dump(raw, fh)
