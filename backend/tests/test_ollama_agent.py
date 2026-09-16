import json
import unittest

import httpx

from app.research.agents import AgentError, OllamaAgent, inline_schema
from app.research.models import Extraction

NO_FLAG = {"evidence_id": "", "quote": "", "present": False}


def extraction_payload():
    return {
        "action_type": "private_placement",
        "counterparties": [{"evidence_id": "E002", "quote": "kutipan yang cukup panjang",
                            "name": "PT Contoh", "relation": "existing_shareholder"}],
        "use_of_funds": [],
        "business_change": NO_FLAG,
        "old_business_divested": NO_FLAG,
        "asset_injection": NO_FLAG,
    }


def agent_with(handler, think=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaAgent("http://ollama.test", "qwen2.5:7b", num_ctx=4096, think=think, client=client)


def capture_payload(think):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": json.dumps(extraction_payload())},
                                         "done_reason": "stop"})

    agent_with(handler, think=think).run("prompt", Extraction)
    return seen["body"]


class OllamaAgentTests(unittest.TestCase):
    def test_thinking_flag_is_sent_only_when_configured(self):
        self.assertIs(capture_payload(False)["think"], False)
        self.assertNotIn("think", capture_payload(None))

    def test_run_sends_inlined_schema_and_parses_structured_output(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": json.dumps(extraction_payload())},
                                             "done_reason": "stop"})

        result = agent_with(handler).run("prompt", Extraction)
        self.assertEqual(result.counterparties[0].name, "PT Contoh")
        self.assertNotIn("$ref", json.dumps(seen["body"]["format"]))
        self.assertEqual(seen["body"]["options"]["num_ctx"], 4096)
        self.assertEqual(seen["body"]["options"]["temperature"], 0)

    def test_output_violating_schema_raises_agent_error(self):
        def handler(request):
            return httpx.Response(200, json={"message": {"content": '{"label": "beli"}'}, "done_reason": "stop"})

        with self.assertRaises(AgentError):
            agent_with(handler).run("prompt", Extraction)

    def test_truncated_output_raises_agent_error(self):
        def handler(request):
            return httpx.Response(200, json={"message": {"content": "{"}, "done_reason": "length"})

        with self.assertRaises(AgentError):
            agent_with(handler).run("prompt", Extraction)

    def test_prompt_larger_than_context_is_rejected_before_calling_model(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(500)

        with self.assertRaises(AgentError):
            agent_with(handler).run("x" * 100_000, Extraction)
        self.assertEqual(calls, [])

    def test_server_error_raises_agent_error(self):
        def handler(request):
            return httpx.Response(500)

        with self.assertRaises(AgentError):
            agent_with(handler).run("prompt", Extraction)

    def test_check_ready_requires_downloaded_model(self):
        def handler(request):
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

        with self.assertRaises(AgentError):
            agent_with(handler).check_ready()

    def test_check_ready_accepts_installed_model(self):
        def handler(request):
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

        agent_with(handler).check_ready()

    def test_unload_asks_ollama_to_release_the_model(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"done": True})

        self.assertTrue(agent_with(handler).unload())
        self.assertEqual(seen["path"], "/api/generate")
        self.assertEqual(seen["body"], {"model": "qwen2.5:7b", "keep_alive": 0})

    def test_failed_unload_is_reported_not_raised(self):
        def handler(request):
            return httpx.Response(500)

        self.assertFalse(agent_with(handler).unload())

    def test_inline_schema_resolves_nested_definitions(self):
        schema = inline_schema(Extraction.model_json_schema())
        self.assertNotIn("$ref", json.dumps(schema))
        relation = schema["properties"]["counterparties"]["items"]["properties"]["relation"]
        self.assertIn("existing_shareholder", relation["enum"])


if __name__ == "__main__":
    unittest.main()
