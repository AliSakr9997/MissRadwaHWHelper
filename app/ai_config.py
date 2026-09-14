"""Per-teacher AI configuration (gitignored, local only).

Stored: data/<profile>/ai_config.json {provider, model, base_url, api_key}.
Reads for display are ALWAYS masked (last 4 chars). The raw key only ever
goes from this file straight into the provider HTTPS call.
"""
from __future__ import annotations

import json
import base64
import os
import ctypes
from ctypes import wintypes

from . import profiles


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _protect(value: str) -> str:
    """Protect API keys with Windows DPAPI; keep a plaintext migration path."""
    if not value:
        return ""
    if os.name != "nt":
        return "plain:" + value
    raw = value.encode("utf-8")
    blob = _Blob(len(raw), ctypes.cast(ctypes.create_string_buffer(raw),
                                       ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob), ctypes.c_wchar_p("MissRadwaHWHelper"),
            None, None, None, 0,
            ctypes.byref(out)):
        raise OSError("Windows could not protect the AI API key")
    try:
        data = ctypes.string_at(out.pbData, out.cbData)
        return "dpapi:" + base64.b64encode(data).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain:"):
        return value[6:]
    if not value.startswith("dpapi:") or os.name != "nt":
        return value
    raw = base64.b64decode(value[6:])
    blob = _Blob(len(raw), ctypes.cast(ctypes.create_string_buffer(raw),
                                       ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not decrypt the AI API key")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def path():
    profiles.ensure_profile()
    return profiles.data_dir() / "ai_config.json"


def load() -> dict:
    p = path()
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                cfg["api_key"] = _unprotect(str(cfg.get("api_key", "")))
                return cfg
            return {}
        except Exception:
            pass
    return {"provider": "openai", "model": "", "base_url": "", "api_key": ""}


def save(provider: str, model: str, api_key: str, base_url: str = "") -> dict:
    cfg = {"provider": (provider or "").lower(), "model": (model or "").strip(),
           "base_url": (base_url or "").strip(), "api_key": _protect(api_key or "")}
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
