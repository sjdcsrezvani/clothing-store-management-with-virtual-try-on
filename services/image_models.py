"""Try-on providers and the cheapest body each model accepts.

One entry per model: `body` picks the request builder, `options` are the only
provider fields sent besides model/prompt/images, `cost` is the display hint.
Edit and text-generation models are separate lists because the endpoints and
model catalogs differ. The settings page renders from this table, so a model
cannot be selected that the service cannot send.
"""

DEFAULT_PROVIDER = "gapgpt"
DEFAULT_MODEL = "gpt-image-2.5-sunburst"

MULTIPART = "multipart"
SEEDREAM_JSON = "seedream_json"
GENERATION_JSON = "generation_json"

# Exact field sets, verified per family (see docs/ if a provider rejects one:
# the try-on debug panel shows the code and the fields that were sent).
_GPT = {"size": "1024x1024", "quality": "low", "output_format": "jpeg", "n": "1"}
_GAPGPT = {"size": "1024x1024", "quality": "low", "output_format": "jpeg", "n": "1"}


def _m(model_id: str, body: str, options: dict | None = None, cost: str = "—") -> dict:
    return {"id": model_id, "body": body, "options": options or {}, "cost": cost}


def _g(model_id: str, cost: str, sizes: dict) -> dict:
    return {"id": model_id, "body": GENERATION_JSON, "options": {}, "cost": cost,
            "sizes": sizes}


# Text-to-image models accept a JSON body, not multipart edits. Sizes stay
# inside each provider's documented range; the output is cropped locally to the
# exact Instagram ratio either way.
_QWEN_SIZES = {"4:5": "1140x1472", "1:1": "1328x1328", "9:16": "928x1664"}
_QWEN3_SIZES = {"4:5": "1152x1440", "1:1": "1440x1440", "9:16": "1152x2048"}
_FLUX_SIZES = {"4:5": "1024x1536", "1:1": "1024x1024", "9:16": "1024x1536"}
_WAN_SIZES = {"4:5": "1152x1440", "1:1": "1440x1440", "9:16": "810x1440"}
_GAPGPT_SIZES = {"4:5": "1024x1024", "1:1": "1024x1024", "9:16": "1024x1024"}


PROVIDERS = {
    "gapgpt": {
        "label": "gapgpt",
        "api_base": "https://api.gapgpt.app/v1",
        "key_setting": "tryon_api_key",
        "models": [
            # GapGPT only serves the edit endpoint for Sunburst; the other
            # catalog ids route to channels that do not accept reference
            # images, so listing them only produces model_not_found failures.
            _m("gpt-image-2.5-sunburst", MULTIPART, _GAPGPT, "$0.006 +"),
        ],
        "generation_models": [
            # Text-to-image only; this route does not take reference images.
            _g("gapgpt/qwen-image-2.1", "$0.03", _GAPGPT_SIZES),
        ],
    },
    "avalai": {
        "label": "AvalAI",
        "api_base": "https://api.avalai.ir/v1",
        "key_setting": "tryon_api_key_avalai",
        "models": [
            # Only models verified on /v1/images/edits with the shop key.
            # Muse is generation-only, Gemini edits need /v1/chat/completions,
            # FLUX Kontext takes one image, and the Qwen/Seedream-4.5 routes
            # this key reaches are unavailable or deprecated.
            _m("gpt-image-2.5-flare", MULTIPART, _GPT, "$0.006 +"),
            _m("gpt-image-2.5-sunburst", MULTIPART, _GPT, "$0.006 +"),
            _m("seedream-5-0-260128", SEEDREAM_JSON, None, "$0.035"),
        ],
        "generation_models": [
            # Text-to-image routes on AvalAI. Kept separate from the edit list
            # so a generation-only model can never be selected for try-on.
            _g("flux.2-pro", "$0.03 +", _FLUX_SIZES),
            _g("wan2.2-t2i-plus", "$0.05", _WAN_SIZES),
            _g("qwen-image-3.0-pro", "$0.04", _QWEN3_SIZES),
            _g("qwen-image-3.0", "$0.04", _QWEN3_SIZES),
            _g("qwen-image-2.0-pro", "$0.075", _QWEN_SIZES),
        ],
    },
}


def resolve_selection(provider: str, model: str) -> tuple[str, dict]:
    """Canonical (provider id, edit model entry). Unknown values fall back to
    the provider default, so a stale Settings row can never break try-on."""
    pid = (provider or "").strip()
    if pid not in PROVIDERS:
        pid = DEFAULT_PROVIDER
    models = PROVIDERS[pid]["models"]
    wanted = (model or "").strip()
    for entry in models:
        if entry["id"] == wanted:
            return pid, entry
    for entry in models:
        if entry["id"] == DEFAULT_MODEL:
            return pid, entry
    return pid, models[0]


def resolve_generation_selection(provider: str, model: str) -> tuple[str, dict]:
    """Canonical (provider id, text-generation entry) from its own list."""
    pid = (provider or "").strip()
    if pid not in PROVIDERS:
        pid = DEFAULT_PROVIDER
    models = PROVIDERS[pid]["generation_models"]
    wanted = (model or "").strip()
    for entry in models:
        if entry["id"] == wanted:
            return pid, entry
    return pid, models[0]


def model_ids(provider: str) -> list[str]:
    pid, _entry = resolve_selection(provider, "")
    return [entry["id"] for entry in PROVIDERS[pid]["models"]]


def generation_model_ids(provider: str) -> list[str]:
    pid, _entry = resolve_generation_selection(provider, "")
    return [entry["id"] for entry in PROVIDERS[pid]["generation_models"]]


def key_setting(provider: str) -> str:
    pid, _entry = resolve_selection(provider, "")
    return PROVIDERS[pid]["key_setting"]


def api_base(provider: str) -> str:
    pid, _entry = resolve_selection(provider, "")
    return PROVIDERS[pid]["api_base"]
