"""AI provider abstraction: the app talks to AIProvider, never to one vendor.

Supported providers (user supplies their own key in the AI config UI/CLI):
  openai, openrouter, groq, mistral, custom (any OpenAI-compatible endpoint,
  e.g. local LM Studio / Ollama / vLLM), anthropic, google (Gemini).

All HTTP via stdlib urllib (no new dependencies). API keys are passed in
memory only — never logged, never committed (stored gitignored per profile).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class ProviderError(Exception):
    """Actionable provider failure (bad key, unknown model, network...)."""


def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            detail = ""
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise ProviderError(f"request failed: {e}") from e


def _extract_json(text: str) -> dict:
    """Parse model output as JSON, tolerating code fences."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    try:
        obj = json.loads(t)
    except Exception as e:
        raise ProviderError(f"model did not return valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProviderError("model JSON must be an object")
    return obj


class AIProvider:
    """Interface: structured-JSON chat. Subclasses implement one HTTP call."""
    name = "base"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 60):
        if not api_key and self.name != "custom":
            raise ProviderError("API key required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat_json(self, system: str, user: str, model: str) -> dict:
        raise NotImplementedError

    def test(self, model: str) -> tuple[bool, str]:
        try:
            out = self.chat_json("Reply with exactly: {\"ok\": true}",
                                 "{\"ping\": 1}", model)
            return (True, "ok" if out.get("ok") else f"unexpected reply: {out}")
        except ProviderError as e:
            return (False, str(e))

    @staticmethod
    def default_models() -> list[str]:
        return []


class OpenAICompatibleProvider(AIProvider):
    """OpenAI chat-completions shape (also serves Groq/Mistral/custom)."""
    name = "openai-compatible"

    def chat_json(self, system: str, user: str, model: str) -> dict:
        body: dict = {"model": model,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}],
                      "temperature": 0}
        if self.name in ("openai", "custom"):
            body["response_format"] = {"type": "json_object"}
        data = _post_json(f"{self.base_url}/chat/completions", body,
                          self._headers(), self.timeout)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from e
        return _extract_json(text)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 60):
        super().__init__(api_key, base_url or "https://api.openai.com/v1", timeout)

    @staticmethod
    def default_models() -> list[str]:
        return ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 60):
        super().__init__(api_key, base_url or "https://openrouter.ai/api/v1", timeout)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "http://127.0.0.1:8000",
                "X-Title": "MissRadwaHWHelper"}

    @staticmethod
    def default_models() -> list[str]:
        return ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet",
                "google/gemini-flash-1.5", "meta-llama/llama-3.1-8b-instruct"]


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 60):
        super().__init__(api_key, base_url or "https://api.groq.com/openai/v1", timeout)

    @staticmethod
    def default_models() -> list[str]:
        return ["llama-3.1-8b-instant", "llama-3.1-70b-versatile"]


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 60):
        super().__init__(api_key, base_url or "https://api.mistral.ai/v1", timeout)

    @staticmethod
    def default_models() -> list[str]:
        return ["mistral-small-latest", "mistral-large-latest"]


class CustomProvider(OpenAICompatibleProvider):
    """Local or third-party OpenAI-compatible endpoint (LM Studio, Ollama...)."""
    name = "custom"

    def __init__(self, api_key: str, base_url: str = "", timeout: int = 120):
        if not base_url:
            raise ProviderError("custom provider needs a base URL "
                                "(e.g. http://localhost:1234/v1)")
        super().__init__(api_key or "not-needed", base_url, timeout)


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def chat_json(self, system: str, user: str, model: str) -> dict:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": 2000, "temperature": 0,
             "system": system + "\nReply with JSON only, no other text.",
             "messages": [{"role": "user", "content": user}]},
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            self.timeout)
        try:
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
        except (AttributeError, TypeError) as e:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from e
        return _extract_json(text)

    @staticmethod
    def default_models() -> list[str]:
        return ["claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"]


class GoogleProvider(AIProvider):
    name = "google"

    def chat_json(self, system: str, user: str, model: str) -> dict:
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={self.api_key}",
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": [{"parts": [{"text": user}]}],
             "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
            None, self.timeout)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from e
        return _extract_json(text)

    @staticmethod
    def default_models() -> list[str]:
        return ["gemini-1.5-flash", "gemini-1.5-pro"]


PROVIDERS: dict[str, type[AIProvider]] = {
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "mistral": MistralProvider,
    "custom": CustomProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
}


def create(provider: str, api_key: str = "", base_url: str = "",
           timeout: int = 60) -> AIProvider:
    cls = PROVIDERS.get((provider or "").lower())
    if cls is None:
        raise ProviderError(f"unknown provider {provider!r} "
                            f"(choose: {', '.join(sorted(PROVIDERS))})")
    return cls(api_key, base_url or "", timeout)
