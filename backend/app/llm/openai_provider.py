from __future__ import annotations

import json

from openai import OpenAI

from app.llm.base import VERDICT_JSON_SCHEMA, build_prompt
from app.pipeline.schema import CandidateEvent, CompanySnapshot, Verdict


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def reason(self, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict:
        prompt = build_prompt(event, snapshot)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": VERDICT_JSON_SCHEMA, "strict": True},
            },
        )
        payload = json.loads(response.choices[0].message.content)
        return Verdict(**payload, provider=self.name)
