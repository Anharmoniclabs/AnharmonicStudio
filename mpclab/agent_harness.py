"""Local-first specialist swarm for safe, auditable studio reviews.

The harness deliberately has no autonomous filesystem, shell, or DAW-control
tools.  A model can return an analysis, but applying a recommendation always
remains a separate user action in the workstation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    id: str
    name: str
    focus: str
    color: str


IDENTITIES: tuple[AgentIdentity, ...] = (
    AgentIdentity("atlas", "Atlas", "conductor · scopes and sequences bounded reviews", "#5dd6c0"),
    AgentIdentity("pulse", "Pulse", "transport · recording, monitoring and takes", "#ff7b72"),
    AgentIdentity("grid", "Grid", "arrangement · clips, scenes, timing and sequence", "#79c0ff"),
    AgentIdentity("wave", "Wave", "sampling · chop, warp, stretch and waveform editing", "#d2a8ff"),
    AgentIdentity("patch", "Patch", "instruments · pads, MIDI, synths and articulation", "#f2cc60"),
    AgentIdentity("vox", "Vox", "vocals · capture, comping, tuning and layers", "#ff9bce"),
    AgentIdentity("mix", "Mix", "mixdown · routing, effects, gain staging and export", "#a5d6ff"),
    AgentIdentity("scout", "Scout", "interface · navigation, clarity and accessibility", "#7ee787"),
    AgentIdentity(
        "sentinel", "Sentinel", "reliability · persistence, regression and safety", "#ffa657"
    ),
    AgentIdentity(
        "bridge", "Bridge", "connectivity · provider routing and local-model health", "#8b949e"
    ),
)
IDENTITY_BY_ID = {identity.id: identity for identity in IDENTITIES}
NEUDO_ROOT = Path("/home/al/NEUDO_LITE_CODER")
LOCAL_FAST_MODEL = NEUDO_ROOT / "models/qwen2.5-coder-0.5b-instruct-q4_0.gguf"
LOCAL_9B_MODEL = NEUDO_ROOT / "models/qwen3.5-9b-q4_k_m.gguf"
LOCAL_LLAMA_CLI = NEUDO_ROOT / "vendor/llama.cpp/build/bin/llama-cli"


@dataclass(slots=True)
class ProviderConfig:
    """Non-secret connection preferences.

    Tokens are exclusively read from the process environment at request time.
    They are neither retained on this object nor saved to disk.
    """

    provider: str = "offline"
    model: str = "local-review"
    endpoint: str = ""

    def validate(self) -> None:
        if self.provider not in {
            "offline",
            "openai",
            "anthropic",
            "openai-compatible",
            "local-fast",
            "local-9b",
            "ollama",
        }:
            raise ValueError("unsupported agent provider")
        if len(self.model.strip()) > 160:
            raise ValueError("model name is too long")
        if len(self.endpoint.strip()) > 500:
            raise ValueError("endpoint is too long")
        if self.endpoint and not self.endpoint.startswith(("https://", "http://")):
            raise ValueError("endpoint must be an http(s) URL")
        if self.provider in {"openai", "anthropic"} and self.endpoint:
            raise ValueError("official providers use their fixed HTTPS endpoint")
        if self.provider == "openai-compatible" and self.endpoint:
            from urllib.parse import urlparse

            parsed = urlparse(self.endpoint)
            if (
                parsed.username
                or parsed.password
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("compatible endpoint must be a loopback local model")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {"provider": self.provider, "model": self.model, "endpoint": self.endpoint}

    @classmethod
    def from_dict(cls, value: object) -> "ProviderConfig":
        raw = value if isinstance(value, dict) else {}
        result = cls(
            provider=str(raw.get("provider", "offline")),
            model=str(raw.get("model", "local-review")),
            endpoint=str(raw.get("endpoint", "")),
        )
        result.validate()
        return result


@dataclass(slots=True)
class AgentJob:
    agent_id: str
    task: str
    context: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    state: str = "queued"
    result: str = ""
    error: str = ""

    def validate(self) -> None:
        if self.agent_id not in IDENTITY_BY_ID:
            raise ValueError("unknown specialist")
        if not self.task.strip() or len(self.task) > 4000:
            raise ValueError("job task must be between 1 and 4000 characters")
        if len(self.context) > 12000:
            raise ValueError("job context is too long")


class ProviderClient:
    """Explicit remote requests; no call occurs during construction or loading."""

    def __init__(self, config: ProviderConfig, environment: dict[str, str] | None = None):
        config.validate()
        self.config = config
        self.environment = os.environ if environment is None else environment

    def review(self, identity: AgentIdentity, task: str, context: str) -> str:
        if self.config.provider == "offline":
            return self._offline(identity, task, context)
        prompt = self._prompt(identity, task, context)
        if self.config.provider in {"local-fast", "local-9b"}:
            return self._local_llama(prompt)
        if self.config.provider == "ollama":
            response = self._post(
                "http://127.0.0.1:11434/api/chat",
                {
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                {},
            )
            message = response.get("message", {})
            text = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(text, str) and text.strip():
                return text.strip()
            raise RuntimeError("Ollama returned no message content")
        if self.config.provider == "openai":
            token = self._required("OPENAI_API_KEY")
            endpoint = self.config.endpoint or "https://api.openai.com/v1/responses"
            payload = {"model": self.config.model, "input": prompt, "store": False}
            response = self._post(endpoint, payload, {"Authorization": f"Bearer {token}"})
            output = response.get("output_text")
            if isinstance(output, str) and output.strip():
                return output.strip()
            output = response.get("output")
            if isinstance(output, list):
                text = "".join(
                    content.get("text", "")
                    for item in output
                    if isinstance(item, dict)
                    for content in item.get("content", [])
                    if isinstance(content, dict) and content.get("type") == "output_text"
                ).strip()
                if text:
                    return text
            raise RuntimeError("OpenAI returned no output text")
        if self.config.provider == "anthropic":
            token = self._required("ANTHROPIC_API_KEY")
            endpoint = self.config.endpoint or "https://api.anthropic.com/v1/messages"
            payload = {
                "model": self.config.model,
                "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}],
            }
            response = self._post(
                endpoint,
                payload,
                {"x-api-key": token, "anthropic-version": "2023-06-01"},
            )
            content = response.get("content")
            if isinstance(content, list):
                text = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                ).strip()
                if text:
                    return text
            raise RuntimeError("Anthropic returned no text content")
        token = self.environment.get("ANHARMONIC_LOCAL_API_KEY", "")
        endpoint = self.config.endpoint.rstrip("/") + "/chat/completions"
        if not self.config.endpoint:
            raise RuntimeError("Set a local OpenAI-compatible endpoint before running this job")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self._post(
            endpoint,
            {"model": self.config.model, "messages": [{"role": "user", "content": prompt}]},
            headers,
        )
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
        raise RuntimeError("compatible endpoint returned no message content")

    def _local_llama(self, prompt: str) -> str:
        model = LOCAL_FAST_MODEL if self.config.provider == "local-fast" else LOCAL_9B_MODEL
        if self.config.provider == "local-9b" and self._available_memory() < 8 * 1024**3:
            raise RuntimeError("Local 9B is guarded: free at least 8 GiB RAM, then retry")
        if not LOCAL_LLAMA_CLI.is_file() or not model.is_file():
            raise RuntimeError("NEUDO local model runtime is not installed")
        try:
            completed = subprocess.run(
                [
                    str(LOCAL_LLAMA_CLI),
                    "-m",
                    str(model),
                    "-p",
                    prompt,
                    "-n",
                    "384",
                    "--temp",
                    "0.2",
                    "--no-display-prompt",
                    "--simple-io",
                    "-c",
                    "2048",
                    "-t",
                    "6",
                    "--single-turn",
                    "--no-warmup",
                    "--no-conversation",
                ],
                text=True,
                capture_output=True,
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("local model timed out") from exc
        if completed.returncode:
            raise RuntimeError("local model failed to generate a review")
        text = completed.stdout.split("<|im_end|>", 1)[0].strip()
        if not text:
            raise RuntimeError("local model returned no text")
        return text

    @staticmethod
    def _available_memory() -> int:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return 0

    def _required(self, name: str) -> str:
        value = self.environment.get(name, "")
        if not value:
            raise RuntimeError(f"{name} is not set in the environment")
        return value

    @staticmethod
    def _prompt(identity: AgentIdentity, task: str, context: str) -> str:
        return (
            f"You are {identity.name}, a music-production specialist for Anharmonic Studio. "
            f"Focus: {identity.focus}.\n"
            "Return a compact audit: observed issues, user impact, and safe suggested next steps. "
            "Do not claim to have changed files, operated the DAW, or used tools.\n\n"
            f"Task:\n{task.strip()}\n\nContext:\n{context.strip() or '(none)'}"
        )

    @staticmethod
    def _offline(identity: AgentIdentity, task: str, context: str) -> str:
        context_note = (
            " Context supplied for review." if context.strip() else " No extra context supplied."
        )
        return (
            f"{identity.name} queued a local-first review of: {task.strip()}."
            f" Focus: {identity.focus}.{context_note} "
            "This offline result is a work brief; select a remote provider only when you want model analysis."
        )

    @staticmethod
    def _post(endpoint: str, payload: dict, headers: dict[str, str]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:  # nosec B310 - explicit URL
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"provider request failed ({exc.code})") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"provider connection failed: {exc.reason}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("provider returned an invalid response")
        return decoded


class AgentHarness:
    """Bounded queue plus non-secret provider preferences for the desktop panel."""

    VERSION = 1

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.config = ProviderConfig()
        self.jobs: list[AgentJob] = []

    def queue(self, agent_id: str, task: str, context: str = "") -> AgentJob:
        job = AgentJob(agent_id=agent_id, task=task.strip(), context=context.strip())
        job.validate()
        self.jobs.append(job)
        return job

    def run(self, job: AgentJob, environment: dict[str, str] | None = None) -> AgentJob:
        job.validate()
        if job.state not in {"queued", "failed", "blocked"}:
            raise ValueError("job is already running or complete")
        job.state, job.error = "running", ""
        try:
            job.result = ProviderClient(self.config, environment).review(
                IDENTITY_BY_ID[job.agent_id], job.task, job.context
            )
            job.state = "complete"
        except RuntimeError as exc:
            job.state, job.error = "blocked", str(exc)
        return job

    def save(self) -> None:
        payload = {"version": self.VERSION, "config": self.config.to_dict()}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def load(self) -> None:
        if not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise ValueError("unsupported agent harness settings")
        self.config = ProviderConfig.from_dict(payload.get("config"))
