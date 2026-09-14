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
                "X-Title": "Classroom HW Helper"}

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

    FREE_MODELS = {
        "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
        "gemini-3-flash-preview",
        "gemma-4-31b-it", "gemma-4-26b-a4b-it",
    }

    EXCLUDED_MODELS = {"gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"}

    FALLBACK_MODELS = [
        {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (Free)", "free": True},
        {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash (Free)", "free": True},
        {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash (Free)", "free": True},
        {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash Lite (Free)", "free": True},
        {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite (Free)", "free": True},
        {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash Preview (Free)", "free": True},
        {"id": "gemma-4-31b-it", "name": "Gemma 4 31B IT (Free)", "free": True},
        {"id": "gemma-4-26b-a4b-it", "name": "Gemma 4 26B A4B IT (Free)", "free": True},
    ]

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

    def list_models(self) -> list[dict]:
        """Fetch models from Google API, filter to generateContent-capable,
        cross-reference against approved free list."""
        url = (f"https://generativelanguage.googleapis.com/v1beta/models"
               f"?key={self.api_key}")
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return list(self.FALLBACK_MODELS)

        result = []
        seen = set()
        for m in data.get("models", []):
            name = m.get("name", "")
            if ":generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            model_id = name.rsplit("/", 1)[-1] if "/" in name else name
            if model_id in self.EXCLUDED_MODELS or model_id in seen:
                continue
            seen.add(model_id)
            is_free = model_id in self.FREE_MODELS
            display = model_id
            if is_free:
                friendly = {
                    "gemini-3.8-flash": "Gemini 3.8 Flash",
                    "gemini-3.6-flash": "Gemini 3.6 Flash",
                    "gemini-3.5-flash": "Gemini 3.5 Flash",
                    "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
                    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
                    "gemini-3-flash-preview": "Gemini 3 Flash Preview",
                    "gemma-4-31b-it": "Gemma 4 31B IT",
                    "gemma-4-26b-a4b-it": "Gemma 4 26B A4B IT",
                }.get(model_id, model_id)
                display = f"{friendly} (Free)"
            result.append({"id": model_id, "name": display, "free": is_free})

        result.sort(key=lambda x: (not x["free"], x["id"]))
        return result if result else list(self.FALLBACK_MODELS)

    @staticmethod
    def default_models() -> list[str]:
        return ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                "gemini-3-flash-preview",
                "gemma-4-31b-it", "gemma-4-26b-a4b-it"]


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
