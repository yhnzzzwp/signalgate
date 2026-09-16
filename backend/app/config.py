from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

Profile = Literal["laptop", "workstation"]

# Hardware profiles change which local model reads the evidence, nothing else. They deliberately leave
# PIPELINE_MAX_EVENTS alone: a faster GPU is no reason to spend more Sectors credits per run.
PROFILES: dict[Profile, dict[str, object]] = {
    "laptop": {},
    "workstation": {
        # Same family as the laptop analyst, so the extraction prompt and JSON schema behave the same.
        "ollama_model": "qwen2.5:14b",
        # Three vendors, read in rotation with one model resident at a time. Reviewers exist to
        # disagree, and models from one family tend to make the same mistake on the same sentence.
        # Nothing here emits reasoning traces: make_agent() only suppresses those for qwen3.
        "ollama_reviewer_models": ["glm4:9b", "gemma3:12b"],
        "ollama_timeout_seconds": 900,
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    signalgate_profile: Profile = "laptop"
    sectors_api_key: str = ""
    sectors_api_enabled: bool = True
    llm_backend: Literal["ollama", "off"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_validator_model: str | None = None
    ollama_reviewer_models: list[str] = Field(default_factory=list, max_length=3)
    ollama_keep_alive: str | int = "5m"
    ollama_offload_between_models: bool = True
    ollama_num_ctx: int = Field(default=16384, ge=4096, le=131072)
    ollama_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    pipeline_max_events: int = Field(default=3, ge=1, le=20)
    research_extraction_attempts: int = Field(default=2, ge=1, le=3)
    research_review_rounds: int = Field(default=1, ge=1, le=3)
    research_validator_mode: Literal["strict", "lenient"] = "strict"
    research_cache_ttl_seconds: int = Field(default=3600, ge=0, le=86400)
    research_max_pages: int = Field(default=4, ge=1, le=12)
    research_context_chars: int = Field(default=3000, ge=500, le=12000)
    research_library_dir: Path = REPO_ROOT / "data" / "library"
    source_cache_mode: Literal["prefer_cache", "refresh", "offline"] = "prefer_cache"
    source_cache_ttl_seconds: int = Field(default=86400, ge=0)
    research_pdf_max_pages: int = Field(default=150, ge=1, le=500)
    research_context_total_chars: int = Field(default=16000, ge=3000, le=32000)
    scan_sources: list[str] = Field(default_factory=lambda: ["https://www.idx.id/en/news/announcement",
                                                          "https://emitennews.com/category/emiten"])
    scan_max_articles: int = Field(default=20, ge=1, le=300)
    scan_max_listing_pages: int = Field(default=3, ge=1, le=50)
    research_source_urls: list[str] = Field(default_factory=list)
    research_cases_dir: Path = REPO_ROOT / "cases"
    signalgate_db_path: str = "./signalgate.db"

    def model_post_init(self, __context) -> None:
        """Apply the profile only where nothing was set explicitly.

        `model_fields_set` holds the fields that actually came from the environment or the .env file,
        so a profile can raise the defaults for the other machine without ever overriding a value the
        operator wrote down. Explicit configuration always wins.
        """
        for field, value in PROFILES[self.signalgate_profile].items():
            if field not in self.model_fields_set:
                setattr(self, field, value)
        # build_provider() takes the reviewer path whenever reviewers exist and never looks at the
        # validator again. Failing here is kinder than silently running a model nobody asked for.
        if self.ollama_reviewer_models and "ollama_validator_model" in self.model_fields_set:
            raise ValueError(
                "OLLAMA_VALIDATOR_MODEL diabaikan saat OLLAMA_REVIEWER_MODELS terisi. Pilih salah satu: "
                "kosongkan OLLAMA_REVIEWER_MODELS untuk memakai satu validator, atau hapus "
                "OLLAMA_VALIDATOR_MODEL dan daftarkan model itu sebagai reviewer."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def sectors_block_reason(settings: Settings) -> str | None:
    if not settings.sectors_api_enabled:
        return ("Mode hemat API aktif (SECTORS_API_ENABLED=false): Sectors tidak dipanggil. "
                "Gunakan `python -m app.scrapling_check` atau replay snapshot untuk pengujian.")
    if not settings.sectors_api_key:
        return "SECTORS_API_KEY diperlukan; Sectors adalah sumber data inti SignalGate."
    return None
