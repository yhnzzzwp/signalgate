from __future__ import annotations

from anthropic import Anthropic

from app.llm.base import VERDICT_JSON_SCHEMA, build_prompt
from app.pipeline.schema import CandidateEvent, CompanySnapshot, Verdict


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def reason(self, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict:
        prompt = build_prompt(event, snapshot)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tools=[{"name": "emit_verdict", "input_schema": VERDICT_JSON_SCHEMA}],
            tool_choice={"type": "tool", "name": "emit_verdict"},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        payload = tool_use.input
        return Verdict(**payload, provider=self.name)
