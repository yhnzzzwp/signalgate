from app.config import Settings
from app.research.agents import OllamaAgent
from app.research.engine import ResearchEngine

# Jejak penalaran merusak JSON ketat yang diharapkan schema, jadi mode berpikir harus dimatikan.
# Daftarnya eksplisit dan bukan heuristik: Ollama hanya menerima opsi `think` untuk model yang
# memang punya mode penalaran, dan mengirimnya ke model lain bisa ditolak. Dulu hanya `qwen3` yang
# ditangani, sehingga mengganti OLLAMA_MODEL ke model penalaran lain gagal tanpa petunjuk.
THINKING_MODEL_PREFIXES = ("qwen3", "deepseek-r1", "glm-5", "glm-4.7", "magistral", "phi4-reasoning")


def make_agent(settings: Settings, model_name: str) -> OllamaAgent:
    return OllamaAgent(
        settings.ollama_base_url,
        model_name,
        settings.ollama_num_ctx,
        settings.ollama_timeout_seconds,
        think=False if model_name.startswith(THINKING_MODEL_PREFIXES) else None,
        keep_alive=settings.ollama_keep_alive,
    )


def build_provider(settings: Settings) -> ResearchEngine:
    if settings.llm_backend != "ollama":
        return ResearchEngine(settings)
    analyst = make_agent(settings, settings.ollama_model)
    if settings.ollama_reviewer_models:
        reviewers = [analyst if name == settings.ollama_model else make_agent(settings, name)
                     for name in dict.fromkeys(settings.ollama_reviewer_models)]
        return ResearchEngine(settings, model=analyst, reviewers=reviewers)
    validator = analyst
    if settings.ollama_validator_model and settings.ollama_validator_model != settings.ollama_model:
        validator = make_agent(settings, settings.ollama_validator_model)
    return ResearchEngine(settings, model=analyst, validator=validator)
