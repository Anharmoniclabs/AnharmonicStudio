import json

import pytest

from mpclab.agent_harness import AgentHarness, IDENTITIES, ProviderClient, ProviderConfig


def test_ten_named_identities_have_distinct_specialties():
    assert len(IDENTITIES) == 10
    assert len({item.id for item in IDENTITIES}) == 10
    assert len({item.name for item in IDENTITIES}) == 10


def test_offline_queue_completes_without_network(tmp_path):
    harness = AgentHarness(tmp_path / "swarm.json")
    job = harness.queue("wave", "Inspect the selected clip warp workflow", "8 bars, 90 BPM")
    harness.run(job)
    assert job.state == "complete"
    assert "Wave" in job.result
    assert "selected clip" in job.result


def test_provider_settings_persist_without_credentials(tmp_path):
    path = tmp_path / "swarm.json"
    harness = AgentHarness(path)
    harness.config = ProviderConfig("openai", "gpt-5", "")
    harness.save()
    raw = path.read_text()
    assert "API_KEY" not in raw
    restored = AgentHarness(path)
    restored.load()
    assert restored.config.provider == "openai"
    assert restored.config.model == "gpt-5"
    assert json.loads(raw)["config"] == {"provider": "openai", "model": "gpt-5", "endpoint": ""}


def test_remote_missing_key_blocks_without_disclosing_secret(tmp_path):
    harness = AgentHarness(tmp_path / "swarm.json")
    harness.config = ProviderConfig("openai", "gpt-5", "")
    job = harness.queue("sentinel", "Review persistence")
    harness.run(job, environment={})
    assert job.state == "blocked"
    assert job.error == "OPENAI_API_KEY is not set in the environment"


def test_prompt_is_audit_only_and_not_a_control_channel():
    text = ProviderClient._prompt(IDENTITIES[0], "Inspect recording", "project summary")
    assert "Do not claim to have changed files" in text
    assert "Inspect recording" in text


def test_invalid_endpoint_is_rejected_before_any_request():
    with pytest.raises(ValueError, match="http"):
        ProviderConfig("openai-compatible", "local", "file:///tmp/model").validate()
    with pytest.raises(ValueError, match="fixed HTTPS"):
        ProviderConfig("openai", "gpt-5", "https://lookalike.example/v1").validate()
    with pytest.raises(ValueError, match="loopback"):
        ProviderConfig("openai-compatible", "local", "https://remote.example/v1").validate()


def test_openai_request_is_non_storing_and_parses_rest_shape(monkeypatch):
    captured = {}

    def fake_post(endpoint, payload, headers):
        captured.update(endpoint=endpoint, payload=payload, headers=headers)
        return {"output": [{"content": [{"type": "output_text", "text": "safe review"}]}]}

    monkeypatch.setattr(ProviderClient, "_post", staticmethod(fake_post))
    client = ProviderClient(ProviderConfig("openai", "gpt-5"), {"OPENAI_API_KEY": "not-printed"})
    assert client.review(IDENTITIES[0], "Inspect routing", "") == "safe review"
    assert captured["payload"]["store"] is False
    assert captured["headers"]["Authorization"] == "Bearer not-printed"


def test_local_9b_is_guarded_before_launching_a_memory_heavy_process(monkeypatch):
    client = ProviderClient(ProviderConfig("local-9b", "Qwen3.5-9B-Q4"))
    monkeypatch.setattr(client, "_available_memory", lambda: 1)
    with pytest.raises(RuntimeError, match="8 GiB"):
        client.review(IDENTITIES[0], "Inspect", "")


def test_ollama_free_cloud_profile_only_calls_loopback(monkeypatch):
    captured = {}

    def fake_post(endpoint, payload, headers):
        captured.update(endpoint=endpoint, payload=payload, headers=headers)
        return {"message": {"content": "review"}}

    monkeypatch.setattr(ProviderClient, "_post", staticmethod(fake_post))
    client = ProviderClient(ProviderConfig("ollama", "gpt-oss:120b-cloud"))
    assert client.review(IDENTITIES[0], "Inspect", "") == "review"
    assert captured["endpoint"] == "http://127.0.0.1:11434/api/chat"
