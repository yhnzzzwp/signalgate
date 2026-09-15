from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.mock_provider import MockProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.anthropic_api_key:
        from app.llm.claude_provider import ClaudeProvider

        return ClaudeProvider(api_key=settings.anthropic_api_key)
    if settings.openai_api_key:
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=settings.openai_api_key)
    if settings.gemini_api_key:
        from app.llm.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=settings.gemini_api_key)
    return MockProvider()
