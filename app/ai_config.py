"""Per-teacher AI configuration (gitignored, local only).

Stored: data/<profile>/ai_config.json {provider, model, base_url, api_key}.
Reads for display are ALWAYS masked (last 4 chars). The raw key only ever
goes from this file straight into the provider HTTPS call.
"""
from __future__ import annotations

import json

from . import profiles


def path():
    profiles.ensure_profile()
    return profiles.data_dir() / "ai_config.json"


def load() -> dict:
    p = path()
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            pass
    return {"provider": "openai", "model": "", "base_url": "", "api_key": ""}


def save(provider: str, model: str, api_key: str, base_url: str = "") -> dict:
    cfg = {"provider": (provider or "").lower(), "model": (model or "").strip(),
           "base_url": (base_url or "").strip(), "api_key": api_key or ""}
    path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return masked()


def masked() -> dict:
    cfg = load()
    key = cfg.get("api_key", "")
    return {"provider": cfg.get("provider", ""), "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""),
            "api_key_set": bool(key),
            "api_key_hint": ("…" + key[-4:]) if len(key) > 4 else ("set" if key else "")}


def credentials() -> tuple[str, str, str]:
    """Return (provider, model, api_key) or raise with setup instructions."""
    from . import ai_providers
    cfg = load()
    if not cfg.get("api_key") and cfg.get("provider") != "custom":
        raise ai_providers.ProviderError(
            "no AI API key configured — open the AI tab (or `ai config set`) first")
    if not cfg.get("model"):
        raise ai_providers.ProviderError("no AI model configured")
    return cfg.get("provider", "openai"), cfg.get("model", ""), cfg.get("api_key", "")
