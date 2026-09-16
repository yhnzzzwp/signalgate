import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import PROFILES, Settings
from app.llm.factory import build_provider


def settings(**overrides):
    """Settings with the repo .env ignored, so a developer's own machine cannot change the result."""
    return Settings(_env_file=None, **overrides)


@patch.dict(os.environ, {}, clear=True)
class ProfileTests(unittest.TestCase):
    def test_laptop_is_the_default_and_changes_nothing(self):
        resolved = settings()
        self.assertEqual(resolved.signalgate_profile, "laptop")
        self.assertEqual(resolved.ollama_model, "qwen2.5:7b")
        self.assertIsNone(resolved.ollama_validator_model)
        self.assertEqual(PROFILES["laptop"], {})

    def test_workstation_raises_the_model_and_the_timeout(self):
        resolved = settings(signalgate_profile="workstation")
        self.assertEqual(resolved.ollama_model, "qwen2.5:14b")
        self.assertEqual(resolved.ollama_validator_model, "qwen3:4b")
        self.assertEqual(resolved.ollama_timeout_seconds, 900)

    def test_an_explicit_value_always_beats_the_profile(self):
        resolved = settings(signalgate_profile="workstation", ollama_model="gpt-oss:20b")
        self.assertEqual(resolved.ollama_model, "gpt-oss:20b")
        self.assertEqual(resolved.ollama_validator_model, "qwen3:4b")  # untouched keys still apply

    def test_an_explicit_value_from_the_environment_also_wins(self):
        with patch.dict(os.environ, {"SIGNALGATE_PROFILE": "workstation", "OLLAMA_NUM_CTX": "8192"}):
            resolved = settings()
        self.assertEqual(resolved.ollama_model, "qwen2.5:14b")
        self.assertEqual(resolved.ollama_num_ctx, 8192)

    def test_the_profile_is_selectable_from_the_environment(self):
        with patch.dict(os.environ, {"SIGNALGATE_PROFILE": "workstation"}):
            self.assertEqual(settings().ollama_model, "qwen2.5:14b")

    def test_an_unknown_profile_is_rejected_rather_than_silently_ignored(self):
        with self.assertRaises(ValidationError):
            settings(signalgate_profile="gpu-temanku")

    def test_no_profile_spends_more_sectors_credits(self):
        """A faster GPU is not a reason to pull more events, and each event costs API credits."""
        for name, values in PROFILES.items():
            self.assertNotIn("pipeline_max_events", values, name)
            self.assertEqual(settings(signalgate_profile=name).pipeline_max_events, 3, name)

    def test_every_profile_key_is_a_real_settings_field(self):
        for name, values in PROFILES.items():
            for field in values:
                self.assertIn(field, Settings.model_fields, f"{name}.{field}")

    def test_the_workstation_profile_reaches_the_built_agents(self):
        provider = build_provider(settings(signalgate_profile="workstation"))
        self.assertEqual(provider.model.name, "ollama:qwen2.5:14b")
        self.assertEqual([reviewer.name for reviewer in provider.reviewers], ["ollama:qwen3:4b"])


if __name__ == "__main__":
    unittest.main()
