import json

from voxera import openrouter_catalog


def test_load_curated_catalog_shape_and_recommendations():
    models = openrouter_catalog.load_curated_openrouter_catalog()

    assert models
    ids = {item["id"] for item in models}
    assert openrouter_catalog.recommended_model_for_slot("primary") in ids
    assert openrouter_catalog.recommended_model_for_slot("fast") in ids
    assert openrouter_catalog.recommended_model_for_slot("reasoning") in ids
    assert openrouter_catalog.recommended_model_for_slot("fallback") in ids


def test_grouped_catalog_by_vendor_contains_major_groups():
    models = openrouter_catalog.load_curated_openrouter_catalog()
    groups = dict(openrouter_catalog.grouped_catalog(models))

    assert "OpenAI" in groups
    assert "Google" in groups
    assert "Anthropic" in groups
    assert "Meta" in groups


def test_refresh_curated_catalog_from_live_normalizes_and_preserves_recommended(
    tmp_path, monkeypatch
):
    curated = {
        "models": [
            {
                "vendor": "OpenAI",
                "id": "google/gemini-3-flash-preview",
                "name": "Old Name",
                "recommended_for": ["primary"],
            }
        ]
    }
    catalog_path = tmp_path / "openrouter_catalog.json"
    catalog_path.write_text(json.dumps(curated), encoding="utf-8")

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {
                        "id": "google/gemini-3-flash-preview",
                        "name": "Gemini 3 Flash",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.15", "completion": "0.60"},
                        "supported_parameters": ["temperature", "max_tokens"],
                    }
                ]
            }

    monkeypatch.setattr(openrouter_catalog.httpx, "get", lambda *args, **kwargs: DummyResponse())
    summary = openrouter_catalog.refresh_curated_catalog_from_live(curated_path=catalog_path)

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    model = payload["models"][0]

    assert summary == {"total": 1, "matched": 1, "missing": 0}
    assert model["name"] == "Gemini 3 Flash"
    assert model["context_length"] == 128000
    assert model["recommended_for"] == ["primary"]
