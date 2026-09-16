import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import PROFILES, Settings
from app.llm.factory import THINKING_MODEL_PREFIXES, build_provider


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
        self.assertEqual(resolved.ollama_reviewer_models, ["glm4:9b", "gemma3:12b"])
        self.assertEqual(resolved.ollama_timeout_seconds, 900)

    def test_an_explicit_value_always_beats_the_profile(self):
        resolved = settings(signalgate_profile="workstation", ollama_model="gpt-oss:20b")
        self.assertEqual(resolved.ollama_model, "gpt-oss:20b")
        self.assertEqual(resolved.ollama_reviewer_models, ["glm4:9b", "gemma3:12b"])  # untouched keys still apply

    def test_reviewers_come_from_three_different_vendors(self):
        """Reviewers exist to disagree; one family would make the same mistake on the same sentence."""
        resolved = settings(signalgate_profile="workstation")
        families = {name.split(":")[0].rstrip("0123456789.") for name in
                    [resolved.ollama_model, *resolved.ollama_reviewer_models]}
        self.assertEqual(len(families), 3, families)

    def test_no_profile_model_is_a_thinking_model(self):
        """Jejak penalaran merusak JSON ketat; profil tidak boleh mengandalkan penekanannya."""
        for name in PROFILES:
            resolved = settings(signalgate_profile=name)
            for model in [resolved.ollama_model, *resolved.ollama_reviewer_models]:
                self.assertFalse(model.startswith(THINKING_MODEL_PREFIXES), f"{name}: {model}")

    def test_thinking_mode_is_suppressed_for_every_known_reasoning_family(self):
        """Dulu hanya qwen3 yang ditangani, jadi model penalaran lain gagal tanpa petunjuk."""
        for prefix in THINKING_MODEL_PREFIXES:
            provider = build_provider(settings(ollama_model=f"{prefix}:8b"))
            self.assertIs(provider.model.think, False, prefix)

    def test_a_plain_model_is_not_sent_the_thinking_option_at_all(self):
        """Ollama menolak opsi `think` pada model yang tidak punya mode penalaran."""
        self.assertIsNone(build_provider(settings(ollama_model="qwen2.5:14b")).model.think)

    def test_a_validator_that_would_be_silently_ignored_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            settings(signalgate_profile="workstation", ollama_validator_model="qwen3:4b")
        self.assertIn("OLLAMA_REVIEWER_MODELS", str(caught.exception))

    def test_a_validator_alone_is_still_allowed(self):
        resolved = settings(ollama_validator_model="qwen3:4b")
        self.assertEqual(resolved.ollama_validator_model, "qwen3:4b")
        self.assertEqual(resolved.ollama_reviewer_models, [])

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

    def test_the_workstation_profile_reaches_the_built_agents_in_order(self):
        provider = build_provider(settings(signalgate_profile="workstation"))
        self.assertEqual(provider.model.name, "ollama:qwen2.5:14b")
        self.assertEqual([reviewer.name for reviewer in provider.reviewers],
                         ["ollama:glm4:9b", "ollama:gemma3:12b"])

    def test_the_rotation_never_holds_two_models_at_once(self):
        """Each turn ends with an explicit evict, so peak VRAM is the largest single model."""
        self.assertTrue(settings(signalgate_profile="workstation").ollama_offload_between_models)


if __name__ == "__main__":
    unittest.main()
