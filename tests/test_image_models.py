"""Provider catalog: text generation and edit models are separate lists."""
from services.image_models import (
    generation_model_ids,
    model_ids,
    resolve_generation_selection,
    resolve_selection,
)


def test_generation_models_are_listed_separately_per_provider():
    assert generation_model_ids("gapgpt") == ["gapgpt/qwen-image-2.1"]
    assert generation_model_ids("avalai") == [
        "flux.2-pro",
        "wan2.2-t2i-plus",
        "qwen-image-3.0-pro",
        "qwen-image-3.0",
        "qwen-image-2.0-pro",
    ]
    assert "flux.2-pro" not in model_ids("avalai")
    assert "qwen-image-3.0-pro" not in model_ids("avalai")


def test_each_resolver_falls_back_inside_its_own_catalog():
    provider, entry = resolve_selection("avalai", "flux.2-pro")
    assert provider == "avalai"
    assert entry["id"] != "flux.2-pro"
    assert entry["id"] in model_ids("avalai")

    provider, entry = resolve_generation_selection("avalai", "seedream-5-0-260128")
    assert provider == "avalai"
    assert entry["id"] == "flux.2-pro"
