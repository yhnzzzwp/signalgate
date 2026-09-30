from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

Profile = Literal["laptop", "workstation"]
FrontierMode = Literal["shadow", "escalation"]

PROFILES: dict[Profile, dict[str, object]] = {
    "laptop": {},
    "workstation": {
        "ollama_model": "qwen2.5:14b",
        "ollama_reviewer_models": ["glm4:9b", "gemma3:12b"],
        "ollama_timeout_seconds": 900,
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / "backend" / ".env", extra="ignore")

    local_only: bool = False
    signalgate_profile: Profile = "laptop"
    sectors_api_key: str = ""
    sectors_api_key_backups: list[str] = Field(default_factory=list)
    sectors_api_enabled: bool = True
    llm_backend: Literal["ollama", "off"] = "ollama"
    # Endpoint dari env. Setelah lokasi GPU diaktifkan dari dashboard (app/runtime.py), pilihan runtime
    # itulah yang dipakai untuk run berikutnya; env tetap jadi bawaan selama belum pernah diaktifkan.
    ollama_base_url: str = "http://127.0.0.1:11434"
    # Ollama di mesin ini (MacBook). Dipakai saat target runtime = "local".
    ollama_local_url: str = "http://127.0.0.1:11434"
    # Token gateway Colab (Authorization: Bearer). Token yang ditempel di dashboard lebih diutamakan.
    ollama_auth_token: SecretStr = SecretStr("")
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
    workflow_directory: Path = REPO_ROOT / "data" / "workflow"
    workflow_analyst_model: str | None = None
    # Lama: satu pembanding. Tetap diterima; kalau terisi, laporan memakai tepat satu pembanding ini.
    workflow_reviewer_model: str | None = None
    # Baru: beberapa pembanding lokal berurutan (maks 3), masing-masing dilepas dari VRAM setelah gilirannya.
    # Kosong = ikut OLLAMA_REVIEWER_MODELS.
    workflow_reviewer_models: list[str] = Field(default_factory=list, max_length=3)
    workflow_num_ctx: int = Field(default=8192, ge=4096, le=131072)
    workflow_num_predict: int = Field(default=1024, ge=256, le=8192)
    workflow_quarters: int = Field(default=5, ge=2, le=12)
    workflow_price_calendar_days: int = Field(default=180, ge=30, le=730)
    workflow_news_days: int = Field(default=90, ge=7, le=365)
    workflow_news_limit: int = Field(default=30, ge=1, le=30)
    workflow_deadline_seconds: int = Field(default=1800, ge=60, le=14400)
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
    scan_directory: Path = REPO_ROOT / "data" / "scans"
    signalgate_db_path: str = str(REPO_ROOT / "backend" / "signalgate.db")

    # --- Reviewer frontier (opsional, berbayar). Mati secara bawaan: tanpa ini perilaku lokal tidak berubah.
    frontier_enabled: bool = False
    frontier_provider: Literal["deepseek"] = "deepseek"
    # Secret backend. SecretStr menyamarkan nilainya di repr, log, dan model_dump; jangan pernah
    # diteruskan ke frontend atau variabel VITE_*.
    deepseek_api_key: SecretStr = SecretStr("")
    frontier_base_url: str = "https://api.deepseek.com"
    frontier_model: str = "deepseek-flash"
    # shadow: hasil frontier hanya disimpan sebagai pembanding. escalation: dipakai lewat aturan
    # rekonsiliasi kode (app/frontier/reconcile.py), tidak pernah langsung menentukan label.
    frontier_mode: FrontierMode = "shadow"
    frontier_reasoning_effort: Literal["low", "high", "max"] = "high"
    # Termasuk token penalaran: thinking mode menghitung reasoning ke completion_tokens.
    frontier_max_tokens: int = Field(default=8192, ge=1024, le=65536)
    frontier_timeout_seconds: int = Field(default=180, ge=10, le=1800)
    # Semua percobaan (termasuk retry) dihitung ke batas ini.
    frontier_max_calls_per_run: int = Field(default=3, ge=0, le=20)
    frontier_max_retries: int = Field(default=1, ge=0, le=3)
    frontier_max_tokens_per_run: int = Field(default=80_000, ge=1_000, le=2_000_000)
    # Batas biaya dalam USD, ditegakkan dengan reservasi konservatif (harga peak, max_tokens penuh)
    # sebelum setiap panggilan. Biaya aktual tetap estimasi dari usage API dan tabel harga berversi.
    # Bawaan disetel untuk saldo prabayar kecil (top-up $2): total $1,80 menyisakan margin untuk
    # selisih estimasi, dan batas harian membuat saldo tidak habis dalam satu hari.
    # Per run $0,06: tiga panggilan dengan reservasi batas atas berbasis byte (±$0,0175 per panggilan pada
    # input maksimum) harus muat, supaya batas run tidak hanya menguji budget_exhausted.
    frontier_max_cost_usd_per_run: float = Field(default=0.06, ge=0, le=50)
    frontier_max_cost_usd_per_day: float = Field(default=0.60, ge=0, le=500)
    # Batas seumur ledger (semua run dan hari di data/frontier/ledger.sqlite). Tidak tahu pemakaian key
    # yang sama di luar SignalGate.
    frontier_max_cost_usd_total: float = Field(default=1.80, ge=0, le=5000)
    # prefer_cache: pakai respons tersimpan bila input identik, panggil API bila tidak ada.
    # offline: hanya respons tersimpan; cache miss dicatat eksplisit, API tidak pernah dipanggil.
    # refresh: selalu panggil API (respons baru tetap disimpan).
    frontier_cache_mode: Literal["prefer_cache", "offline", "refresh"] = "prefer_cache"
    # Replay snapshot Sectors bebas kredit Sectors, tapi tidak otomatis bebas biaya LLM. Bawaan:
    # run replay hanya membaca cache frontier (seperti mode offline).
    frontier_calls_on_replay: bool = False
    # Batas ukuran prompt frontier (karakter). Paket bukti dipilih agar muat; prompt yang tetap lebih besar
    # tidak dikirim. Sekaligus membatasi reservasi biaya input per panggilan.
    frontier_max_input_chars: int = Field(default=24_000, ge=4_000, le=400_000)
    frontier_directory: Path = REPO_ROOT / "data" / "frontier"
    # Pilihan lokasi GPU dan mode frontier dari dashboard (config.json) + token Colab (secrets.json, 0600).
    runtime_directory: Path = REPO_ROOT / "data" / "runtime"

    @property
    def sectors_api_keys(self) -> list[str]:
        """Key utama diikuti key cadangan, tanpa duplikat, untuk SectorsClient(...)."""
        keys = [self.sectors_api_key] if self.sectors_api_key else []
        keys.extend(key for key in self.sectors_api_key_backups if key and key not in keys)
        return keys

    def model_post_init(self, __context) -> None:
        """Apply the profile only where nothing was set explicitly.

        `model_fields_set` holds the fields that actually came from the environment or the .env file,
        so a profile can raise the defaults for the other machine without ever overriding a value the
        operator wrote down. Explicit configuration always wins.
        """
        if self.signalgate_db_path != ":memory:":
            path = Path(self.signalgate_db_path).expanduser()
            if not path.is_absolute():
                self.signalgate_db_path = str(REPO_ROOT / "backend" / path)
        for field, value in PROFILES[self.signalgate_profile].items():
            if field not in self.model_fields_set:
                setattr(self, field, value)
        if self.ollama_reviewer_models and "ollama_validator_model" in self.model_fields_set:
            raise ValueError(
                "OLLAMA_VALIDATOR_MODEL diabaikan saat OLLAMA_REVIEWER_MODELS terisi. Pilih salah satu: "
                "kosongkan OLLAMA_REVIEWER_MODELS untuk memakai satu validator, atau hapus "
                "OLLAMA_VALIDATOR_MODEL dan daftarkan model itu sebagai reviewer."
            )
        if self.workflow_reviewer_models and self.workflow_reviewer_model:
            raise ValueError(
                "WORKFLOW_REVIEWER_MODEL (satu pembanding, format lama) dan WORKFLOW_REVIEWER_MODELS (daftar) "
                "terisi bersamaan. Pilih salah satu: hapus WORKFLOW_REVIEWER_MODEL untuk memakai daftar, atau "
                "kosongkan WORKFLOW_REVIEWER_MODELS untuk tetap memakai satu pembanding."
            )

    def public_frontier(self) -> dict:
        """Ringkasan konfigurasi frontier yang aman ditampilkan: tidak pernah memuat nilai key."""
        from app.frontier.pricing import MODEL_VERSIONS

        return {"enabled": self.frontier_enabled, "provider": self.frontier_provider, "model": self.frontier_model,
                "model_version": MODEL_VERSIONS.get(self.frontier_model),
                "mode": self.frontier_mode, "reasoning_effort": self.frontier_reasoning_effort,
                "key_configured": bool(self.deepseek_api_key.get_secret_value().strip()),
                "cache_mode": self.frontier_cache_mode, "calls_on_replay": self.frontier_calls_on_replay,
                "limits": {"max_calls_per_run": self.frontier_max_calls_per_run,
                           "max_retries": self.frontier_max_retries,
                           "max_tokens_per_call": self.frontier_max_tokens,
                           "max_tokens_per_run": self.frontier_max_tokens_per_run,
                           "max_cost_usd_per_run": self.frontier_max_cost_usd_per_run,
                           "max_cost_usd_per_day": self.frontier_max_cost_usd_per_day,
                           "max_cost_usd_total": self.frontier_max_cost_usd_total,
                           "max_input_chars": self.frontier_max_input_chars,
                           "timeout_seconds": self.frontier_timeout_seconds}}


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


def frontier_config_issue(settings: Settings) -> str | None:
    """Alasan frontier tidak bisa memanggil API, atau None. Bukan error start: hasil lokal tetap terbit."""
    if not settings.frontier_enabled:
        return None
    if not settings.deepseek_api_key.get_secret_value().strip() and settings.frontier_cache_mode != "offline":
        return ("FRONTIER_ENABLED=true tetapi DEEPSEEK_API_KEY kosong. Isi key di backend/.env (hanya di "
                "backend, jangan di frontend/VITE_*), atau set FRONTIER_CACHE_MODE=offline untuk memutar ulang "
                "respons tersimpan saja. Sampai itu, frontier berstatus unavailable dan hasil lokal tetap dipakai.")
    return None
