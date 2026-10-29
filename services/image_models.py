"""Try-on providers and the cheapest body each model accepts.

One entry per model: `body` picks the request builder, `options` are the only
provider fields sent besides model/prompt/images, `cost` is the display hint.
The settings page renders from this table, so a model cannot be selected that
the service cannot send.
"""

DEFAULT_PROVIDER = "gapgpt"
DEFAULT_MODEL = "gpt-image-2"

MULTIPART = "multipart"
SEEDREAM_JSON = "seedream_json"
MUSE_JSON = "muse_json"

# Exact field sets, verified per family (see docs/ if a provider rejects one:
# the try-on debug panel shows the code and the fields that were sent).
_GPT = {"size": "1024x1024", "quality": "low", "output_format": "jpeg", "n": "1"}
_GEMINI = {"size": "1024x1024"}
_QWEN3 = {"size": "1024x1024", "n": "1"}
_QWEN2 = {"n": "1"}                      # native 2K output, flat price
_KONTEXT = {"size": "1024x1024", "n": "1", "response_format": "b64_json"}
_GAPGPT = {"size": "1024x1024", "quality": "low", "output_format": "jpeg", "n": "1"}


def _m(model_id: str, body: str, options: dict | None = None, cost: str = "—") -> dict:
    return {"id": model_id, "body": body, "options": options or {}, "cost": cost}


PROVIDERS = {
    "gapgpt": {
        "label": "gapgpt",
        "api_base": "https://api.gapgpt.app/v1",
        "key_setting": "tryon_api_key",
        "models": [
            _m("gpt-image-2", MULTIPART, _GAPGPT, "$0.006 +"),
            _m("gpt-image-2.5-flare", MULTIPART, _GAPGPT, "$0.006 +"),
            _m("gpt-image-2.5-sunburst", MULTIPART, _GAPGPT, "$0.006 +"),
            _m("gpt-image-1", MULTIPART, _GAPGPT, "$0.011 +"),
            _m("gpt-image-1-mini", MULTIPART, _GAPGPT, "cheapest"),
            _m("gapgpt/qwen-image-2.1", MULTIPART, _GAPGPT),
            _m("gemini-3-pro-image-preview", MULTIPART, _GAPGPT),
            _m("gemini-2.5-flash-image", MULTIPART, _GAPGPT),
            _m("flux-1-schnell", MULTIPART, _GAPGPT),
        ],
    },
    "avalai": {
        "label": "AvalAI",
        "api_base": "https://api.avalai.ir/v1",
        "key_setting": "tryon_api_key_avalai",
        "models": [
            _m("muse-image-1.0", MUSE_JSON, None, "$0.0115"),
            _m("gpt-image-2", MULTIPART, _GPT, "$0.006 +"),
            _m("gpt-image-2.5-flare", MULTIPART, _GPT, "$0.006 +"),
            _m("gpt-image-2.5-sunburst", MULTIPART, _GPT, "$0.006 +"),
            _m("gpt-image-1", MULTIPART, _GPT, "$0.011 +"),
            _m("gpt-image-1-mini", MULTIPART, _GPT, "cheapest"),
            _m("gemini-3.1-flash-lite-image", MULTIPART, _GEMINI, "$0.0336"),
            _m("gemini-3.1-flash-image", MULTIPART, _GEMINI, "$0.0672"),
            _m("gemini-3-pro-image", MULTIPART, _GEMINI, "$0.134"),
            _m("gemini-2.5-flash-image", MULTIPART, _GEMINI, "$0.04"),
            _m("seedream-5-0-260128", SEEDREAM_JSON, None, "$0.035"),
            _m("seedream-4-5-251128", SEEDREAM_JSON, None, "$0.04"),
            _m("qwen-image-3.0-pro", MULTIPART, _QWEN3, "$0.04 + $0.003/ref"),
            _m("qwen-image-3.0", MULTIPART, _QWEN3, "$0.04 + $0.003/ref"),
            _m("qwen-image-2.0-pro", MULTIPART, _QWEN2, "$0.075"),
            _m("qwen-image-2.0", MULTIPART, _QWEN2, "$0.035"),
            _m("qwen-image-edit-plus", MULTIPART, _QWEN3, "$0.03"),
            _m("flux.2-pro", MULTIPART, _QWEN3, "$0.03"),
            _m("flux.1-kontext-pro", MULTIPART, _KONTEXT, "$0.04"),
        ],
    },
}


def resolve_selection(provider: str, model: str) -> tuple[str, dict]:
    """Canonical (provider id, model entry). Unknown values fall back to the
    provider default, so a stale Settings row can never break try-on."""
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


def model_ids(provider: str) -> list[str]:
    pid, _entry = resolve_selection(provider, "")
    return [entry["id"] for entry in PROVIDERS[pid]["models"]]


def key_setting(provider: str) -> str:
    pid, _entry = resolve_selection(provider, "")
    return PROVIDERS[pid]["key_setting"]


def api_base(provider: str) -> str:
    pid, _entry = resolve_selection(provider, "")
    return PROVIDERS[pid]["api_base"]
