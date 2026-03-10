from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

CATALOG_SOURCE_URL = "https://openrouter.ai/api/v1/models"
CATALOG_PATH = Path(__file__).resolve().parent / "data" / "openrouter_catalog.json"

VENDOR_ORDER: tuple[str, ...] = (
    "OpenAI",
    "Google",
    "Anthropic",
    "Meta",
    "Qwen",
    "Mistral",
    "DeepSeek",
    "xAI",
    "Alibaba",
    "Others",
)

SLOT_RECOMMENDED_MODEL: dict[str, str] = {
    "primary": "openai/gpt-4o-mini",
    "fast": "google/gemini-2.5-flash",
    "reasoning": "anthropic/claude-3.7-sonnet",
    "fallback": "meta-llama/llama-3.3-70b-instruct",
}


CatalogEntry = dict[str, Any]


def infer_vendor(*, model_id: str, model_name: str | None = None) -> str:
    model_id_l = model_id.lower()
    model_name_l = (model_name or "").lower()
    if model_id_l.startswith("openai/"):
        return "OpenAI"
    if model_id_l.startswith("google/") or "gemini" in model_id_l:
        return "Google"
    if model_id_l.startswith("anthropic/") or "claude" in model_id_l:
        return "Anthropic"
    if model_id_l.startswith("meta-") or "llama" in model_id_l:
        return "Meta"
    if model_id_l.startswith("qwen/"):
        return "Qwen"
    if model_id_l.startswith("mistralai/") or "mistral" in model_id_l:
        return "Mistral"
    if model_id_l.startswith("deepseek/"):
        return "DeepSeek"
    if model_id_l.startswith("x-ai/") or "grok" in model_name_l:
        return "xAI"
    if model_id_l.startswith("alibaba/"):
        return "Alibaba"
    return "Others"


def recommended_model_for_slot(slot_key: str) -> str:
    return SLOT_RECOMMENDED_MODEL.get(slot_key, SLOT_RECOMMENDED_MODEL["primary"])


def load_curated_openrouter_catalog(path: Path = CATALOG_PATH) -> list[CatalogEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        raise ValueError("curated catalog must contain a top-level models[] list")

    models: list[CatalogEntry] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        models.append(
            {
                "vendor": str(
                    item.get("vendor")
                    or infer_vendor(model_id=model_id, model_name=item.get("name"))
                ),
                "id": model_id,
                "name": str(item.get("name") or model_id),
                "context_length": item.get("context_length"),
                "pricing_prompt": item.get("pricing_prompt"),
                "pricing_completion": item.get("pricing_completion"),
                "supported_parameters": list(item.get("supported_parameters") or []),
                "recommended_for": list(item.get("recommended_for") or []),
            }
        )
    return models


def grouped_catalog(models: list[CatalogEntry]) -> list[tuple[str, list[CatalogEntry]]]:
    grouped: dict[str, list[CatalogEntry]] = {vendor: [] for vendor in VENDOR_ORDER}
    for model in models:
        vendor = str(model.get("vendor") or "Others")
        if vendor not in grouped:
            vendor = "Others"
        grouped[vendor].append(model)

    result: list[tuple[str, list[CatalogEntry]]] = []
    for vendor in VENDOR_ORDER:
        items = sorted(grouped[vendor], key=lambda item: str(item.get("id", "")))
        if items:
            result.append((vendor, items))
    return result


def _normalize_live_model(
    entry: dict[str, Any], *, recommended_for: list[str], vendor: str
) -> CatalogEntry:
    pricing_raw = entry.get("pricing")
    pricing: dict[str, Any] = pricing_raw if isinstance(pricing_raw, dict) else {}
    params = entry.get("supported_parameters")
    parameters = (
        [str(item) for item in params if isinstance(item, str)] if isinstance(params, list) else []
    )
    model_id = str(entry.get("id") or "").strip()
    name = str(entry.get("name") or model_id)
    context_length = (
        entry.get("context_length") if isinstance(entry.get("context_length"), int) else None
    )
    return {
        "vendor": vendor or infer_vendor(model_id=model_id, model_name=name),
        "id": model_id,
        "name": name,
        "context_length": context_length,
        "pricing_prompt": str(pricing.get("prompt")) if pricing.get("prompt") is not None else None,
        "pricing_completion": str(pricing.get("completion"))
        if pricing.get("completion") is not None
        else None,
        "supported_parameters": sorted(set(parameters)),
        "recommended_for": sorted(set(recommended_for)),
    }


def refresh_curated_catalog_from_live(
    *,
    curated_path: Path = CATALOG_PATH,
    source_url: str = CATALOG_SOURCE_URL,
) -> dict[str, int]:
    curated_payload = json.loads(curated_path.read_text(encoding="utf-8"))
    curated_models = curated_payload.get("models") if isinstance(curated_payload, dict) else None
    if not isinstance(curated_models, list):
        raise ValueError("curated catalog must contain a top-level models[] list")

    response = httpx.get(source_url, timeout=20.0)
    response.raise_for_status()
    live_payload = response.json()
    live_data = live_payload.get("data") if isinstance(live_payload, dict) else None
    if not isinstance(live_data, list):
        raise ValueError("OpenRouter models response must contain top-level data[]")

    live_by_id: dict[str, dict[str, Any]] = {}
    for item in live_data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            live_by_id[model_id] = item

    refreshed_models: list[CatalogEntry] = []
    matched = 0
    missing = 0
    for item in curated_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        recommended_for = [str(slot) for slot in item.get("recommended_for") or []]
        vendor = str(item.get("vendor") or "")
        live_entry = live_by_id.get(model_id)
        if live_entry is None:
            missing += 1
            refreshed_models.append(
                {
                    "vendor": vendor
                    or infer_vendor(model_id=model_id, model_name=item.get("name")),
                    "id": model_id,
                    "name": str(item.get("name") or model_id),
                    "context_length": item.get("context_length"),
                    "pricing_prompt": item.get("pricing_prompt"),
                    "pricing_completion": item.get("pricing_completion"),
                    "supported_parameters": list(item.get("supported_parameters") or []),
                    "recommended_for": sorted(set(recommended_for)),
                }
            )
            continue

        matched += 1
        refreshed_models.append(
            _normalize_live_model(live_entry, recommended_for=recommended_for, vendor=vendor)
        )

    refreshed_payload = {
        "source_url": source_url,
        "refreshed_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "models": refreshed_models,
    }
    curated_path.write_text(json.dumps(refreshed_payload, indent=2) + "\n", encoding="utf-8")
    return {"total": len(refreshed_models), "matched": matched, "missing": missing}
