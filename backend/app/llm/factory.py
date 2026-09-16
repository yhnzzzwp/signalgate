from app.config import Settings
from app.research.agents import OllamaAgent
from app.research.engine import ResearchEngine


def make_agent(settings: Settings, model_name: str) -> OllamaAgent:
    return OllamaAgent(
        settings.ollama_base_url,
        model_name,
        settings.ollama_num_ctx,
        settings.ollama_timeout_seconds,
        think=False if model_name.startswith("qwen3") else None,
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
