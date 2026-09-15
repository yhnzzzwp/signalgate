from __future__ import annotations

import json

from google import genai
from google.genai import types

from app.llm.base import VERDICT_JSON_SCHEMA, build_prompt
from app.pipeline.schema import CandidateEvent, CompanySnapshot, Verdict


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def reason(self, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict:
        prompt = build_prompt(event, snapshot)
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VERDICT_JSON_SCHEMA,
            ),
        )
        payload = json.loads(response.text)
        return Verdict(**payload, provider=self.name)
