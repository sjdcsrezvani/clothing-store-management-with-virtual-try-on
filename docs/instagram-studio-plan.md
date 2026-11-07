# Instagram Studio — Approved Implementation Plan

**Status:** Phases 1–5 implemented; live smoke blocked by AvalAI quota (HTTP 429)
**Date:** 2026-11-06
**Repo:** `/Users/chubbyshady/Developer/raykid_store/referral-app-clean`
**Feature:** Generate professional Instagram product photography inside the RaiKids POS.

**Source plans merged into this document:**

- `/Users/chubbyshady/Downloads/instagram_studio_feature_plan.md`
- `/Users/chubbyshady/Downloads/instagram-image-generation-plan.md`

This is the canonical plan. If a future session hits a token limit, start there and read this file first. Do not re-litigate the decisions in section 2 unless the owner asks.

---

## Phase 1 progress

- [x] Prompt builder and prompt tests (`services/instagram_prompts.py`, `tests/test_instagram_prompts.py`)
- [x] `InstagramGeneration` model + test DB cleanup
- [x] Independent generator and image normalization
- [x] Worker/job support (`instagram_image`, scheduler release)
- [x] Quota helpers (`instagram_daily_limit`)
- [x] Studio service (validation, reference collection, history helpers)
- [x] Router, templates, navigation, icon
- [x] Settings field
- [x] Packaging hidden imports
- [x] Navigation tests updated
- [x] Full test run: `1521 passed`; the only 4 failures are the pre-existing `test_visual_hover` PDF checks, which need the missing macOS `Quartz` module (`ModuleNotFoundError: No module named 'Quartz'`) and are unrelated to this feature
- [ ] Live smoke test — blocked externally: AvalAI answers HTTP 429 `quota_exceeded` for both `gpt-image-2.5-sunburst` and `seedream-5-0-260128` with the saved key. The request reaches the provider and the failure is classified correctly. Retried after the full test run on 2026-11-06: still 429. Retry when the account has quota/credit.

## Phase 2 progress

- [x] `product_brief` scenario in validation, service, prompt builder, and route
- [x] Prompt version bumped to `2026-11-06.2`
- [x] Brief tab with optional scene, required 10+ character brief, and neutral campaign clause when no scene is chosen
- [x] Variant «جزئیات تکمیلی» (`ProductVariant.tryon_details`) injected into both garment and brief prompts; no new column
- [x] Variant edit page copy now tells staff the details feed Instagram Studio, and the studio shows the details that will be injected
- [x] History scenario filter and labels for both modes
- [x] Tests for prompts, service validation, routes, the brief tab, and the variant-details end-to-end path
- [x] Full test run: `1534 passed`; only the 4 pre-existing `test_visual_hover` Quartz failures
- [ ] Live smoke test — still blocked by the same AvalAI HTTP 429 `quota_exceeded`

## Phase 3 progress

- [x] `ShopModel` table with lazy `ensure_shop_models` seeding of the eight fixed slots
- [x] Owner-only `/admin/settings/shop-models` library with eight upload cards
- [x] Upload validation: PNG/JPG/WebP, max 10 MB, min 512 px longest edge, storage compression, random filenames, old file removed on replace
- [x] Settings link «مدل‌های فروشگاه»
- [x] Pose, background, framing, lighting, and composition libraries in `services/instagram_prompts.py`
- [x] `model_scene` prompt: Image 1 is the model reference; garment references follow; age-safe child/teen/adult rules; garment dominance; variant details included
- [x] Studio tab «مدل و ژست» with model picker, pose/background controls, framing/lighting/composition, full-garment toggle, and optional note
- [x] History filter and card labels for `model_scene`
- [x] Prompt version bumped to `2026-11-06.3`
- [x] Tests: seeding, upload validation/replacement, owner gate, prompt libraries, model service path, route generation, history filter
- [x] Full test run: `1558 passed`; only the 4 pre-existing `test_visual_hover` Quartz failures
- [ ] Live smoke test — still blocked by the same AvalAI HTTP 429 `quota_exceeded`

### Phase 3 implementation notes (for the next session)

- No `lifespan` change: `ensure_shop_models(db)` is lazy from the settings page and the model studio tab.
- The model image is resolved again when the job runs; a deleted file fails fast rather than silently dropping the model.
- The worker and `InstagramGeneration` schema did not change; `model_slot`, `pose_key`, and `background_key` were already in the Phase 1 table.
- Framing/lighting/composition live in the rendered prompt; the table keeps only the three fields the original plan defined.

## Phase 4 progress

- [x] `text` scenario in prompt builder, service validation, route, and history filter
- [x] Tab «کمپین متنی» works without selecting a product
- [x] Required 10+ character campaign brief; optional aspect ratio; no reference images
- [x] No-reference request goes to `/images/generations`; no new table or worker branch
- [x] Same quota, history, download, delete, and logo-on-download path
- [x] History cards show the text brief and «کمپین متنی»
- [x] Prompt version bumped to `2026-11-06.4`
- [x] Tests: text prompt contract, short-brief refusal, reference-free job, tab without product, history filter/card
- [ ] Live smoke test — still blocked by the same AvalAI HTTP 429 `quota_exceeded`

### Phase 4 implementation notes (for the next session)

- `create_generation` accepts `product=None` and `variant=None` only for `scenario=text`; every other scenario still requires both.
- The text form does not post product or variant, so the route never asks the product picker for them.
- The duplicate guard compares the trimmed stored brief for text instead of a variant.
- `normalize_image` still crops the provider output to the exact selected Instagram size.
- Text campaigns use `instagram_generation_model` from the provider's generation-only catalog; every image scenario keeps `tryon_model`. The provider and API key stay shared.
- Generation models post JSON to `/v1/images/generations` with no `image` field. A rejected size retries once at `1024x1024`, then the local crop still lands on the exact Instagram size.

## Phase 5 progress

- [x] «تولید تنوع» on completed result panels
- [x] Variation reuses product, variant, references, scene, aspect, note, logo, and model controls
- [x] Job payload now keeps framing, lighting, composition, and full-garment choice for later variations
- [x] Usage counts for today, the last 7 days, and the last 30 days
- [x] Active model and approximate per-image cost remain on the studio page
- [x] Actionable Persian error categories with provider codes kept in the log
- [x] Removed 2026-11-07: history page, delete route, and favorites; the owner downloads images and keeps them, so outputs are purged after 7 days
- [x] `docs/instagram-quality-checklist.md`
- [x] `docs/instagram-studio.md`
- [ ] Live smoke test — still blocked by the same AvalAI HTTP 429 `quota_exceeded`

### Phase 5 implementation notes (for the next session)

- Variation reuses the existing generate route; no new job type, table, or worker branch.
- Old generations without the newer payload keys fall back to the default framing/lighting/composition values.
- Reloading the studio with `?job_id=` rebuilds the full result context, so variation, retry, and logo-download actions survive a refresh (regression test in `tests/test_instagram_routes.py`).
- Full test run on 2026-11-06 after that fix: `1578 passed`; only the 4 pre-existing `test_visual_hover` Quartz failures.

### Phase 1 implementation notes (for the next session)

- Added `tests/test_instagram_routes.py` (role gate, enqueue, duplicate guard, quota, history, delete, download).- `tests/test_themes.py` / `tests/test_silent_defaults.py` now seed one `InstagramGeneration` + job and excuse the two non-page endpoints (`jobs/{job_id}`, `history/{generation_id}/download`).
- The generic image worker is imported in `main.py` through the existing `tryon_worker_task` alias, so no lifespan change was needed.
- The generator refuses if any selected reference file has disappeared, rather than silently dropping it.
- The live `tryon_model` in the local DB is `gpt-image-2.5-sunburst` (not Seedream); both code paths are implemented, but smoke could not run because of the external 429.

---

## 1. Goal

Let a manager or owner:

1. Pick a product and **one color variant**.
2. Attach up to three reference photos from that variant's own gallery.
3. Pick a garment-photography scene and an Instagram aspect ratio.
4. Generate one commercial-quality image asynchronously.
5. Review it, download it, and find it later in history.

The garment must remain recognizably the same product: same color, silhouette, print, trims, and details. Creative styling is secondary.

---

## 2. Decisions locked

| Decision | Choice |
|---|---|
| First release | Product garment scenes only |
| Color variants | **One color per generation**; no multi-color group shot in the first release |
| Reference photos | Up to **3 per variant**: primary + additional views |
| Source of reference photos | The variant's own gallery (`VariantImage`, fallback `ProductVariant.image_path`); never silently use a different color's image |
| Provider / key | Share the existing try-on configuration (`tryon_provider`, `tryon_api_key`, `tryon_api_key_avalai`) |
| Model selection | Separate edit model (`tryon_model`) and text-to-image model (`instagram_generation_model`); generation-only models never appear in the edit list |
| Active provider today | AvalAI + `seedream-5-0-260128` |
| Instagram daily limit | Separate `instagram_daily_limit`, default **20/day** |
| Roles | `manager` and `owner`; cashier must not see or open the page |
| History | Keep all generations; owner can delete; managers can view/download |
| Prompt visibility | Collapsible final-prompt preview for manager+ |
| Logo | Optional toggle, **off by default**; reuse `composite_logo()`; no AI-rendered text |
| Aspect ratios | `4:5` default, `1:1`, `9:16` |
| Outputs per request | **One**; “generate variation” re-runs the same inputs as a new generation |
| User brief language | English templates; staff type English briefs; pass through as-is, no translation |
| Model library | 8 fixed AI-generated model slots (girl/boy at 2, 7, 13, 18) in Phase 3 |
| Model reference storage | Normal public `static/uploads/shop_models/` with random filenames; all models are AI, so no consent workflow |
| Navigation | New **«محتوا»** section → **«استودیو اینستاگرام»**, manager+ |
| Worker | One image worker handles `tryon` and `instagram_image`; no second poll loop |

---

## 3. Current codebase facts

These are the integration points. Verify them again before editing if the code has moved.

| Area | File / symbol | Notes |
|---|---|---|
| Provider catalog | `services/image_models.py` | `PROVIDERS`, `SEEDREAM_JSON`, `MULTIPART`, `resolve_selection()`, `key_setting()`, `api_base()` |
| Try-on generator | `services/image_gen.py` | `get_tryon_selection()`, `ImageGenerationError`, `classify_response()`, `composite_logo()`, `ImageGenService` |
| Important limitation | `ImageGenService.generate()` | Requires **2+ reference images** and always posts to `/images/edits`. Product-only and text-only need a separate path. Do **not** force Instagram into this method. |
| Jobs | `services/jobs.py` | `enqueue()`, `claim_next(db, job_type)`, `process_one()`, `complete()`, `fail()` |
| Image worker | `services/tryon_worker.py` | 10s poll, concurrency 2, fail-fast on `ImageGenerationError`, requeue local errors |
| Scheduler | `services/scheduler.py` | Claims any job; explicitly releases `tryon` jobs back to the dedicated worker. Must also release `instagram_image`. |
| App startup | `main.py` | `Base.metadata.create_all()` + `upgrade()` + `_apply_missing_columns()`; starts the worker in `lifespan`; includes routers around lines 489–499 |
| Models | `models.py` | `BackgroundJob` has `job_type`, `payload`, `status`, `error_message`, `result_path`, `result_url`, `completed_at` |
| Settings registry | `routers/admin.py` | `SETTINGS_NUMERIC_RULES`, `SETTINGS_TABS`, `_SETTINGS_FIELD_TABS`; messaging tab around line 339 of `templates/admin/settings.html` |
| Navigation | `services/navigation.py` | `NAV_SECTIONS`, `PARENTS`; labels must be Persian and contain no Latin letters |
| Icons | `templates/base.html` | SVG sprite `<symbol id="icon-...">` |
| Navigation tests | `tests/test_navigation.py` | Currently asserts exactly **five** sections; adding «محتوا» requires updating that test and `KNOWN_PATHS` |
| Upload storage | `static/uploads/` | Existing pattern: random filename + public URL |
| Image compression | `services/image_storage.py` | `compress_for_storage()` for uploaded reference images |
| Packaging | `store_management.spec` | Hardcoded `hiddenimports`; new router/services must be added or the frozen app can crash |
| Try-on saved images | `models.GeneratedImage` | Unrelated table; do not reuse it for Instagram |

**Current live settings (no secrets):**

```text
tryon_provider = avalai
tryon_model    = seedream-5-0-260128
tryon_daily_limit = 50
store_name     = رای کیدز
store_instagram = raykids_official
```

---

## 4. Provider facts (AvalAI docs)

Source: <https://docs.avalai.ir/en/api-reference/images> and <https://docs.avalai.ir/en/examples/generate_images_with_seedream_4>

- `seedream-5-0-260128` supports both:
  - `POST /v1/images/generations` — text-only.
  - `POST /v1/images/edits` — one or more reference images.
- Seedream supports custom sizes, for example `1440x2560` (9:16) and `3024x1296`.
- Multi-image blending is supported by sending an `image` array.
- Seedream edit JSON body used by the existing code:

```json
{
  "model": "seedream-5-0-260128",
  "prompt": "...",
  "image": ["data:image/jpeg;base64,..."],
  "size": "1440x1800",
  "response_format": "b64_json",
  "sequential_image_generation": "disabled",
  "watermark": false
}
```

- `gpt-image-2.5-flare` and `gpt-image-2.5-sunburst` also support generations and edits. Their common sizes are `1024x1024`, `1024x1536`, and `1536x1024`; exact 4:5 and 9:16 may need a local crop.
- Responses may contain `data[0].b64_json`, `data[0].url`, or a data URL. Handle all three.
- Content-policy refusals may arrive as HTTP 400/403 or as a 200 body without an image. Reuse the existing classification markers.

Because the active model is Seedream 5, we can request the exact Instagram size. Still implement the crop fallback for when the shared model is switched.

---

## 5. Roadmap

| Phase | Scope | Outcome |
|---|---|---|
| **1** | Garage scenes + foundation | Manager can generate a garment image from one variant, review, download, history |
| **2** | Product + creative brief | Same picker with a free-text brief and no required scene |
| **3** | Model photoshoot | 8 AI model slots, pose/background libraries, age-safe model scenes |
| **4** | Text-only | Campaign image from text, no product |
| **5** | Lean polish | Variation button, cost/usage display, better errors, favorites, QA checklist, docs |

Implement **Phase 1 only** first. Do not build later phases opportunistically; the schema and UI should stay small.

---

## 6. Phase 1 — Garment scenes

### 6.1 Data model

Add to `models.py`:

```python
class InstagramGeneration(Base):
    __tablename__ = "instagram_generations"

    id = Column(Integer, primary_key=True, index=True)
    created_by_staff_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False, index=True)
    scenario = Column(String(30), nullable=False, default="garment", index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True, index=True)
    garment_mode = Column(String(40), nullable=True)
    model_slot = Column(String(20), nullable=True)
    pose_key = Column(String(40), nullable=True)
    background_key = Column(String(40), nullable=True)
    aspect_ratio = Column(String(10), nullable=False, default="4:5")
    user_text = Column(Text, nullable=True)
    rendered_prompt = Column(Text, nullable=False)
    prompt_version = Column(String(30), nullable=False)
    provider = Column(String(40), nullable=True)
    model = Column(String(80), nullable=True)
    job_id = Column(Integer, ForeignKey("background_jobs.id"), nullable=True, unique=True, index=True)
    is_favorite = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
```

Design rules:

- **No duplicate `status`, `error_message`, `image_path`, or `completed_at`.** Join `BackgroundJob` through `job_id` for status, errors, output path, output URL, and completion time. One source of truth.
- One generation row = one output image.
- `is_favorite` is defined now, but the toggle UI is Phase 5. This avoids a schema change later.
- New tables are created automatically by `Base.metadata.create_all(bind=engine)` in `main.py`; no `migrations.py` change is required for a new table. (`_apply_missing_columns` is only for columns added to existing tables.)
- Add relationships only if useful for loading; `joinedload` in history queries is enough.
- Do not put API keys or secrets in this table or in the job payload.

### 6.2 Settings and quota

Add `instagram_daily_limit`:

- `routers/admin.py` → `SETTINGS_NUMERIC_RULES`:

```python
"instagram_daily_limit": (0, 1000, "سقف تولید روزانه اینستاگرام"),
```

- `routers/admin.py` → `_SETTINGS_FIELD_TABS`:

```python
"instagram_daily_limit": "messaging",
```

- `templates/admin/settings.html`, messaging tab, beside the existing `tryon_daily_limit` field. Use the same `num()` helper and `{{ tip(...) }}` pattern.

Quota helpers in `services/security.py`:

```python
def _instagram_day_start():
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

def instagram_daily_remaining(db) -> int:
    from services._common import get_setting_int
    limit = get_setting_int(db, "instagram_daily_limit", 20)
    used = db.query(BackgroundJob).filter(
        BackgroundJob.job_type == "instagram_image",
        BackgroundJob.created_at >= _instagram_day_start(),
        BackgroundJob.status.in_(("pending", "processing", "completed")),
    ).count()
    return max(0, limit - used)

def instagram_can_generate(db) -> bool:
    return instagram_daily_remaining(db) > 0
```

Why count `pending`/`processing` too: it reserves quota at enqueue time so two quick requests cannot overshoot the cap. A failed job is excluded and frees its slot. The count is persistent across restarts, unlike the in-memory try-on counter.

Duplicate-submit guard (server-side, same route):

```python
recent = datetime.now(timezone.utc) - timedelta(minutes=2)
existing = db.query(InstagramGeneration).join(
    BackgroundJob, InstagramGeneration.job_id == BackgroundJob.id
).filter(
    InstagramGeneration.created_by_staff_id == user.id,
    InstagramGeneration.variant_id == variant.id,
    InstagramGeneration.garment_mode == mode,
    BackgroundJob.status.in_(("pending", "processing")),
    InstagramGeneration.created_at >= recent,
).first()
if existing:
    return error("این تصویر همین حالا در صف است؛ چند لحظه صبر کنید.")
```

Also disable the submit button client-side after the first click.

### 6.3 Prompt builder

Create `services/instagram_prompts.py`.

```python
IG_PROMPT_VERSION = "2026-11-06.1"

SCENES = {
    "hanger": ("آویزان روی جاکرکش", "..."),
    "flat_lay": ("چیدمان تخت", "..."),
    "ghost_mannequin": ("مانکن نامرئی", "..."),
    "detail_closeup": ("نمای نزدیک جزئیات", "..."),
    "fabric_texture": ("نمای نزدیک بافت پارچه", "..."),
    "folded_stack": ("تاشده فروشگاهی", "..."),
    "wooden_hanger_wall": ("جاکرکش چوبی روی دیوار", "..."),
    "packshot": ("پکشات یکدست", "..."),
    "back_view": ("نمای پشت/پهلو", "..."),
    "soft_shadow_studio": ("نور و سایه نرم استودیویی", "..."),
}

ASPECTS = {
    "4:5": {"label": "۴:۵ (فید اینستاگرام)", "seedream_size": "1440x1800", "target": (1440, 1800), "gpt_size": "1024x1536"},
    "1:1": {"label": "۱:۱ (مربع)", "seedream_size": "1440x1440", "target": (1440, 1440), "gpt_size": "1024x1024"},
    "9:16": {"label": "۹:۱۶ (استوری/ریلز)", "seedream_size": "1440x2560", "target": (1440, 2560), "gpt_size": "1024x1536"},
}
```

Public builder:

```python
def build_garment_prompt(*, product_name: str, variant_color: str,
                         scene_key: str, aspect_ratio: str,
                         reference_count: int, user_note: str = "") -> str:
    ...
```

Prompt sections, in order:

1. **Base commercial brief**

```text
Professional commercial fashion product photograph for a children's clothing boutique's Instagram.
Photorealistic, high-end catalog quality, accurate fabric behavior, clean composition, soft flattering light,
sharp focus on the product. No watermark, no text, no extra logos. The presentation must be family-friendly
and age-appropriate.
```

2. **Garment fidelity block**

```text
Treat the supplied product reference image(s) as the only source of truth for the garment.
Reproduce the exact same garment, not a redesigned interpretation. Preserve its exact visible color,
silhouette, proportions, cut, neckline, sleeves, hem, seams, closures, pockets, print, embroidery,
trims, and placement of details. Do not add, remove, recolor, simplify, embellish, or invent garment
features. Do not substitute a similar garment. Do not add logos, labels, text, accessories, or extra
pieces unless they are explicitly visible in the references. If a detail is not visible, do not invent it.
The garment must remain the clear visual subject.
```

3. **Reference mapping**

- 1 image: `Image 1 is the primary product photo of the exact garment.`
- 2 images: `Image 1 is the primary product photo. Image 2 is an additional view of the same exact garment.`
- 3 images: `Image 1 is the primary product photo. Image 2 is an additional view. Image 3 is a detail view of the same exact garment.`

Never label an image “back” unless the UI actually knows it is a back view. Generic “additional view” avoids wrong mapping.

4. **Scene clause** (one of the 10 below)

- `hanger`: “The exact garment hangs naturally on a simple wooden hanger, centered, retail display style, full garment visible, soft studio background.”
- `flat_lay`: “The exact garment is arranged neatly flat on a clean neutral surface, photographed from directly above (top-down), balanced composition, full garment visible.”
- `ghost_mannequin`: “The exact garment is presented as a ghost mannequin / invisible mannequin: a hollow, naturally shaped 3D garment with no visible person, body, skin, head, or mannequin, studio e-commerce style, full garment visible.”
- `detail_closeup`: “Macro close-up of the garment's construction details: seams, stitching, buttons, zipper, cuffs, hem, or trims. Sharp focus, shallow depth of field, do not invent details that are not visible.”
- `fabric_texture`: “Extreme close-up of the visible fabric texture and weave/knit, soft raking side light, sharp focus, do not invent fiber composition.”
- `folded_stack`: “The exact garment is neatly folded in a tidy retail stack, edges aligned, boutique presentation, enough of the garment visible to identify it.”
- `wooden_hanger_wall`: “The exact garment hangs on a wooden hanger against a minimal wall, clean negative space, soft daylight, full garment visible.”
- `packshot`: “Centered e-commerce packshot on a seamless pure white or soft gray background, soft contact shadow, full garment visible, no props.”
- `back_view`: “The back of the exact garment is emphasized, showing the back silhouette, seams, and any visible back details. Use the supplied additional/back reference if present. Full garment visible.”
- `soft_shadow_studio`: “Professional studio product shot with soft directional light and a gentle natural contact shadow, clean seamless background, full garment visible, premium commercial look.”

5. **Aspect / composition clause**

- `4:5`: “Vertical 4:5 Instagram feed composition. Keep the full garment inside the frame with generous margins on all sides. Do not crop off important garment edges.”
- `1:1`: “Square 1:1 Instagram composition. Keep the full garment inside the frame with generous margins. Do not crop off important garment edges.”
- `9:16`: “Vertical 9:16 story/reel cover composition. Keep the full garment inside the safe area with generous margins. Do not crop off important garment edges.”

6. **Negative constraints** (append, since Seedream has no separate negative field in this integration)

```text
Avoid: altered garment design or color; invented seams, buttons, pockets, prints, labels, or logos;
missing garment details; extra garments; duplicated limbs; distorted anatomy; floating garment;
impossible fabric physics; cluttered background; props covering the product; cropped garment edges
in full-product shots; text; watermark; signature; fake brand marks; plastic skin; cartoon or CGI look.
```

7. **User note** (last, clearly delimited)

```text
Additional creative direction from the shop (must not override garment fidelity, composition, or safety rules):
"{user_note}"
```

8. **Final checklist**

```text
Before finishing, verify: garment color matches the reference; silhouette and proportions match;
print and embroidery placement match; no invented details; the full garment is visible; the background
is clean; there is no text or watermark; fabric and anatomy are realistic.
```

Rules:

- Do not concatenate raw UI labels into the prompt; translate selections into photographic directions.
- Do not claim fabric composition unless the shop supplied it.
- Keep the user note in a delimited section so it cannot override fidelity/safety.
- Version the template and store `IG_PROMPT_VERSION` on every generation.

### 6.4 Independent generator

Create `services/instagram_gen.py`. This deliberately does **not** modify `ImageGenService.generate()` or the try-on flow.

Reuse:

- `get_tryon_selection(db)` — provider/model/key resolved at execution time.
- `ImageGenerationError` and `classify_response()` from `services.image_gen`.
- `composite_logo()` from `services.image_gen`.
- `resolve_selection()`, `api_base()`, `SEEDREAM_JSON`, `MULTIPART` from `services.image_models`.

Copy, do not refactor:

- The 30-line `_prep` logic (EXIF transpose, alpha flatten, resize longest edge to 1024, JPEG encode). Keeping a copy is intentional: the try-on path must not regress.
- The response extraction logic for `b64_json` / `url` / data URL.

Public function:

```python
def generate_instagram_image(
    *,
    prompt: str,
    reference_paths: list[str],
    aspect_ratio: str,
    provider: str,
    model: str,
    api_key: str,
    logo: bool = False,
    logo_path: str = "",
    job_id: int | None = None,
) -> bytes:
    ...
```

Endpoint choice:

- `reference_paths` empty → `POST /v1/images/generations` (Phase 4).
- `reference_paths` non-empty → `POST /v1/images/edits`.

Seedream body (edits):

```json
{
  "model": "seedream-5-0-260128",
  "prompt": "...",
  "image": ["data:image/jpeg;base64,..."],
  "size": "1440x1800",
  "response_format": "b64_json",
  "sequential_image_generation": "disabled",
  "watermark": false
}
```

Seedream body (generations) omits `image`.

GPT Image body (edits, multipart):

```text
model=<model id>
prompt=<prompt>
image[]=<file> ...        # 1..3 files
size=1024x1536            # 4:5 and 9:16 fallback; 1024x1024 for 1:1
quality=medium            # better than try-on's low; Instagram is commercial output
output_format=jpeg
n=1
```

GPT Image body (generations): JSON with `model`, `prompt`, `size`, `quality`, `output_format`, `n`.

Size strategy:

1. If the model is Seedream, request `ASPECTS[ratio]["seedream_size"]`.
2. If the provider rejects the custom size, retry once with `size="2K"` and crop locally.
3. For GPT Image models, request `ASPECTS[ratio]["gpt_size"]`, then crop to the exact target.
4. Always run local normalization, even when the provider returns the right size.

Output normalization:

- Open with PIL, `ImageOps.exif_transpose`, convert to RGB.
- Center-crop to the target aspect ratio.
- Resize to `ASPECTS[ratio]["target"]` with LANCZOS.
- If `logo` is true and a logo path is configured, composite with a proportional width:
  `logo_width=max(120, int(image.width * 0.12))`.
- Save JPEG quality 92, optimize.
- Thumbnail: max 400px, same directory under `thumbs/`.
- Filenames: `ig_{uuid4().hex[:12]}.jpg`.
- Paths: `static/uploads/instagram/` and `static/uploads/instagram/thumbs/`.
- URLs: `/static/uploads/instagram/<file>` and `/static/uploads/instagram/thumbs/<file>`.

Error handling:

- No API key → `ImageGenerationError("کلید ... ذخیره نشده است.", category="auth", code="missing_key")`.
- No references for an edit mode → validation error before enqueue (Phase 1 always requires at least one).
- Policy refusal / rate limit / auth / server / timeout → reuse `classify_response`, then map to Persian messages in the router/result panel.
- Do not retry a billable provider call automatically. The worker fails fast, same as try-on.
- No API key or prompt secret in logs or job payloads.

Debug logging:

- Optionally record attempts with `services.tryon_debug.record(job_id=..., ...)` so failures are diagnosable from the existing log infrastructure. Do not build a second debug panel in Phase 1.

### 6.5 Worker and jobs

Generalize `services/tryon_worker.py` in place.

`services/jobs.py`:

```python
def claim_next(db, job_type: str | tuple[str, ...] | None = None):
    ...
    if job_type:
        if isinstance(job_type, tuple):
            query = query.filter(BackgroundJob.job_type.in_(job_type))
        else:
            query = query.filter(BackgroundJob.job_type == job_type)
```

`services/tryon_worker.py`:

```python
IMAGE_JOB_TYPES = ("tryon", "instagram_image")

async def image_worker_task() -> None:
    ...
    job = claim_next(session, IMAGE_JOB_TYPES)
    ...
    if job.job_type == "tryon":
        tryon_record_generation(ip=queued_ip)
    # Instagram quota is counted from completed/pending jobs; no callback needed.

# Backward-compatible name for existing tests/imports.
tryon_worker_task = image_worker_task
```

Keep the same semantics:

- `ImageGenerationError` → mark job failed immediately, never requeue (billable failure).
- Local errors (payload, disk, DB) → `fail()` with backoff.
- Concurrency stays 2.
- Resolve provider/model/key at execution time, not enqueue time.

`services/jobs.py` → `process_one()` add a branch:

```python
if job.job_type == "instagram_image":
    from services.instagram_gen import generate_instagram_image
    from services.image_gen import get_tryon_selection
    import asyncio
    payload = json.loads(job.payload)
    provider_name, model_name, api_key = get_tryon_selection(db)
    image_bytes = await asyncio.to_thread(
        generate_instagram_image,
        prompt=payload["prompt"],
        reference_paths=payload.get("reference_paths") or [],
        aspect_ratio=payload.get("aspect_ratio", "4:5"),
        provider=provider_name,
        model=model_name,
        api_key=api_key,
        logo=payload.get("logo", False),
        logo_path=payload.get("logo_path", ""),
        job_id=job.id,
    )
    output_path = Path(payload["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    job.result_path = str(output_path)
    job.result_url = payload["result_url"]
    db.commit()
    return True
```

`services/scheduler.py` must release both job types:

```python
if job.job_type in {"tryon", "instagram_image"}:
    job.status = "pending"
    job.locked_at = None
    db.commit()
    break
```

`main.py` lifespan:

- Import the generalized worker (keep the old name as an alias or update the import).
- No second worker task.

### 6.6 Routes

Create `routers/instagram_studio.py` with `APIRouter(prefix="/admin")`.

```text
GET  /admin/instagram-studio
     params: q, product_id, variant_id, msg, err
     - manager+ guard
     - product search by name/barcode
     - variant list for the selected product
     - reference thumbnails for the selected variant
     - daily remaining, scene list, aspect list, current model/cost hint
     - empty state when no product is selected

POST /admin/instagram-studio/generate
     form: csrf_token, product_id, variant_id, image_paths (multi),
           garment_mode, aspect_ratio, user_text, logo (optional)
     - manager+ guard
     - validate everything (section 6.9)
     - check quota and duplicate guard
     - build prompt
     - create InstagramGeneration row
     - enqueue BackgroundJob("instagram_image", payload)
     - set generation.job_id, commit
     - redirect to /admin/instagram-studio?msg=... or return the result panel

GET  /admin/instagram-studio/jobs/{job_id}
     - manager+ guard
     - JSON: {job_id, status, image_url, error}
     - HTMX request: render partials/instagram_live.html
     - completed/failed: HX-Redirect to the studio page or swap the result

GET  /admin/instagram-studio/history
     params: scenario, product_id, favorite, page
     - manager+ guard
     - join InstagramGeneration + BackgroundJob
     - newest first, paginated
     - prompt <details> for manager+
     - owner sees delete buttons

POST /admin/instagram-studio/history/{generation_id}/delete
     - owner guard
     - delete output + thumbnail files
     - delete generation row
     - keep the BackgroundJob row (history/audit) or delete it; prefer keeping the job row

GET  /admin/instagram-studio/history/{generation_id}/download
     params: logo=no|yes
     - manager+ guard
     - serve the clean image, or composite the logo on the fly
     - FileResponse / Response with a safe filename
```

Phase 5 adds:

```text
POST /admin/instagram-studio/history/{generation_id}/favorite
     - manager+ guard
     - toggle is_favorite
```

“Generate variation” does **not** need a new route: render a form with the original hidden inputs and post it to the existing `/generate` endpoint.

### 6.7 UI

Add:

- `templates/admin/instagram_studio.html`
- `templates/admin/instagram_history.html`
- `templates/partials/instagram_live.html`
- `templates/admin/settings_shop_models.html` (Phase 3)
- Add `icon-camera` (or `icon-instagram`) to the SVG sprite in `templates/base.html`.

Studio page structure:

1. Header: title, daily remaining chip, current model and approximate cost hint.
2. Product search form (`q`), server-rendered results. Each result links to `?product_id=...`.
3. If a product is selected: variant dropdown (color/size), reference thumbnails with checkboxes (max 3), scene chips (10), aspect ratio chips, optional English note, optional logo checkbox.
4. Generate button. Disabled when required fields are missing; disabled after click.
5. Result panel: queued/processing/completed/failed; image preview; download; “generate variation” (Phase 5).
6. Link to history.

History page:

- Grid/list with thumbnail, product/variant, scene, aspect, date, provider/model, favorite star (Phase 5), download, delete (owner), prompt `<details>`.
- Filters: scenario, product, favorite.
- Empty state.

UI rules:

- Persian/RTL labels; prompts remain English.
- Keep the existing CSS/component patterns; no new framework.
- Required fields: product, variant, at least one reference image, scene, aspect.
- Show a warning for `back_view` when fewer than 2 references: “برای بهترین نتیجه عکس پشت محصول را اضافه کنید.”
- Show a warning for `detail_closeup`/`fabric_texture` when only a full-product photo is attached.
- Make it clear that generation costs money and counts against the daily limit.

### 6.8 Navigation

`services/navigation.py`:

- Add a new section **«محتوا»** after «فروش» (before «کالا و انبار»):

```python
{"key": "content", "label": "محتوا", "items": (
    _item("/admin/instagram-studio", "استودیو اینستاگرام", "camera", "instagram-studio", "manager"),
)},
```

- Add a parent for child pages:

```python
("/admin/instagram-studio/", "instagram-studio", "استودیو اینستاگرام"),
```

- The studio page `<h1>` must be exactly «استودیو اینستاگرام».
- The history page `<h1>` can be «تاریخچه استودیو اینستاگرام»; its breadcrumb parent is the studio item.
- Cashiers must not see the section. The existing “empty section disappears” behavior handles this.

`tests/test_navigation.py` updates:

- Change the five-section assertion to six:

```python
assert [section["label"] for section in NAV_SECTIONS] == [
    "فروش", "محتوا", "کالا و انبار", "مشتریان و باشگاه", "مالی", "مدیریت",
]
```

- Add to `KNOWN_PATHS`:

```python
"/admin/instagram-studio", "/admin/instagram-studio/history",
```

- Add `"/admin/instagram-studio"` to manager `REACHABLE` if needed by the test.
- Keep the cashier section assertion unchanged (the new section is manager+).
- Ensure the new labels contain no Latin letters.
- The new icon must exist in `templates/base.html`.

### 6.9 Validation rules

At enqueue:

- Role is manager or owner.
- `product_id` exists and is active.
- `variant_id` exists, belongs to the product, and is active.
- At least one reference image. Every selected path must belong to that variant's gallery (`VariantImage.image_path`) or equal `variant.image_path`. **Reject arbitrary filesystem paths.**
- Maximum 3 reference images. Reject duplicates, keep deterministic order.
- If the variant has no images: hard error:
  “برای این تنوع عکس محصول ثبت نشده است — ابتدا تصویر تنوع را در محصولات ثبت کنید.”
  Do **not** silently fall back to the product-level image or another color.
- `garment_mode` is one of the 10 scene keys.
- `aspect_ratio` is one of `4:5`, `1:1`, `9:16`.
- `user_text` optional; trim; cap at e.g. 1000 characters.
- Daily quota not exhausted.
- No duplicate pending/processing generation for the same staff + variant + scene within 2 minutes.
- Provider key is present. If missing, show the existing Persian “کلید ذخیره نشده” message before enqueue.

At worker execution:

- Resolve provider/model/key again; a rotated key applies to queued jobs.
- If the reference file disappeared, fail as a local error (requeue) or a non-retryable validation error; prefer fail-fast with a clear message.
- If the provider returns a 200 with no image, classify as `no_image`/`refusal` and fail the job.

### 6.10 History, download, delete

History query:

```python
rows = (
    db.query(InstagramGeneration)
    .options(joinedload(InstagramGeneration.job))
    .order_by(InstagramGeneration.created_at.desc())
    .all()
)
```

Because there is no status column on the generation, read `row.job.status`, `row.job.error_message`, `row.job.result_url`, `row.job.result_path`, `row.job.completed_at`.

Download:

- `logo=no` → serve the stored JPEG.
- `logo=yes` → read the stored JPEG, call `composite_logo(bytes, logo_path, logo_width=...)`, return PNG/JPEG bytes. No extra stored file.

Delete (owner only):

- Remove `job.result_path` and the thumbnail path (derive thumbnail from the filename under `thumbs/`).
- Delete the `InstagramGeneration` row.
- Keep the `BackgroundJob` row for audit. If the job is still pending/processing, block deletion with a message; do not delete a running job.
- Use `log_action(db, "instagram_delete", ...)`.

### 6.11 Files to add / change

**Add**

```text
services/instagram_prompts.py
services/instagram_gen.py
services/instagram_studio.py        # validation, reference collection, history helpers
routers/instagram_studio.py
templates/admin/instagram_studio.html
templates/admin/instagram_history.html
templates/partials/instagram_live.html
tests/test_instagram_prompts.py
tests/test_instagram_studio.py
tests/test_instagram_worker.py
tests/test_instagram_images.py
```

**Change**

```text
models.py                  # InstagramGeneration
services/jobs.py           # claim_next tuple support + instagram_image branch
services/tryon_worker.py   # generalize to image_worker_task + alias
services/scheduler.py      # release instagram_image too
services/security.py       # instagram_daily_remaining / instagram_can_generate
main.py                    # include router; start generalized worker
services/navigation.py     # «محتوا» section + parent
templates/base.html        # icon-camera
routers/admin.py           # instagram_daily_limit rule + tab mapping
templates/admin/settings.html  # daily-limit input
tests/test_navigation.py   # six sections + new paths
store_management.spec      # hiddenimports for new router/services
```

**Packaging note:** add these to `hiddenimports` in `store_management.spec`:

```python
"routers.instagram_studio",
"services.instagram_gen",
"services.instagram_prompts",
"services.instagram_studio",
"services.tryon_worker",
```

Templates and static files are already collected as whole directories.

### 6.12 Tests

`tests/test_instagram_prompts.py`

- Snapshot each of the 10 scenes contains its scene clause, the fidelity block, the negatives, and the aspect clause.
- Reference mapping for 1, 2, and 3 images.
- User note is delimited and appears last.
- No contradictory “full body” + “close-up” combinations.
- Prompt version is stable and stored.

`tests/test_instagram_studio.py`

- Cashier cannot open the page (403/redirect).
- Manager and owner can open it with an empty catalog.
- Missing product/variant/scene/aspect is rejected.
- Selected image paths must belong to the variant; a path from another variant is rejected.
- Variant with no image is rejected with the Persian message.
- More than 3 references is rejected.
- Quota exhausted is rejected.
- Duplicate pending generation is rejected.
- Valid request creates one `InstagramGeneration` and one `instagram_image` `BackgroundJob`.
- History lists the generation; manager can view; owner can delete; cashier cannot.
- Delete removes the output file and row; a running job cannot be deleted.

`tests/test_instagram_worker.py`

- `claim_next` accepts a tuple of job types.
- The worker claims `instagram_image` and completes it with a fake generator.
- `ImageGenerationError` fails the job immediately and does not requeue.
- Local errors requeue with backoff.
- The scheduler releases `instagram_image` jobs back to the worker.
- Quota counts pending/processing/completed jobs; a failed job frees its slot.

`tests/test_instagram_images.py`

- Output dimensions are exactly 1440x1800, 1440x1440, and 1440x2560.
- Thumbnail exists and is <= 400px.
- Logo toggle composites without changing the stored clean file.
- Non-image provider bytes raise a classified error.

`tests/test_navigation.py`

- Six sections in order.
- New paths have exactly one active nav item.
- Manager sees the studio item; cashier does not.
- Studio page title, h1, and breadcrumb agree.
- The new page opens for manager/owner with no data.

Run:

```bash
.venv/bin/python -m pytest tests/test_instagram_prompts.py tests/test_instagram_studio.py \
  tests/test_instagram_worker.py tests/test_instagram_images.py tests/test_navigation.py -q
```

Also run the existing try-on tests to prove no regression:

```bash
.venv/bin/python -m pytest tests/test_tryon_phase1.py -q
```

### 6.13 Definition of done (Phase 1)

- A manager can search a product, pick one color variant, see up to 3 reference thumbnails, choose one of the 10 scenes and an aspect ratio, add an optional English note, and generate.
- The job goes pending → processing → completed; the image appears in the result panel.
- The result can be downloaded with and without the logo.
- History shows the image, product/variant, scene, aspect, provider/model, and the final prompt.
- The owner can delete a completed generation and its files.
- The cashier cannot see or open the feature.
- The 20/day quota is enforced and survives an app restart.
- Duplicate clicks do not create multiple billable jobs.
- All new tests pass; existing try-on tests pass.
- `store_management.spec` includes the new modules.

---

## 7. Phase 2 — Product + creative brief

Goal: generate a campaign/lifestyle image from one product variant plus a free-text brief.

- Reuse the Phase 1 picker and reference handling.
- Add a mode/tab: `scenario=product_brief`.
- Required: product, variant, at least one reference, `user_text` (min 10 chars).
- Optional: scene (same 10) and aspect.
- If no scene is selected, use a neutral “premium Instagram campaign” composition clause.
- Prompt: base + fidelity + optional scene + user brief + negatives.
- History filter by scenario.
- Validation and quota identical.
- No new table, no new worker branch.

Definition of done: a manager can write “soft morning light, minimal beige studio, back-to-school mood” and get a product-faithful campaign image.

---

## 8. Phase 3 — Model photoshoot

Goal: put the exact garment on one of 8 AI-generated shop models, in a controlled pose/background.

### 8.1 `ShopModel` table

```python
class ShopModel(Base):
    __tablename__ = "shop_models"

    id = Column(Integer, primary_key=True)
    slot_key = Column(String(20), unique=True, nullable=False, index=True)
    label_fa = Column(String(80), nullable=False)
    image_path = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                      onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    updated_by_staff_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
```

Seed the 8 slots if missing (lazy `ensure_shop_models(db)` called from the settings page or lifespan):

```text
girl_2   دختر ۲ ساله
girl_7   دختر ۷ ساله
girl_13  دختر ۱۳ ساله
girl_18  دختر ۱۸ ساله
boy_2    پسر ۲ ساله
boy_7    پسر ۷ ساله
boy_13   پسر ۱۳ ساله
boy_18   پسر ۱۸ ساله
```

All references are **AI-generated**. No real-person consent workflow. Store images under `static/uploads/shop_models/` with random filenames.

### 8.2 Settings UI

- Owner-only `GET/POST /admin/settings/shop-models`.
- 8 slots, each showing the current image or an empty upload box.
- Upload validation: JPEG/PNG/WebP, max 10 MB, min 512px on the longest edge; use `compress_for_storage`; random filename; delete the old file on replace.
- Only `manager`? No: model library management is owner-only. Generation is manager+.
- Link from the Settings page: «مدل‌های فروشگاه».

### 8.3 Pose and background libraries

Use **Plan 1's full libraries** (source document section 3, Mode D). Implement them as code constants in `services/instagram_prompts.py`, not database tables.

Pose categories:

- Standing: front relaxed, 3/4 turn, side profile, back view, one foot forward/walking, hands relaxed.
- Seated/lifestyle: bench/chair, clean studio block, casual seated.
- Movement: gentle walking, small natural turn, light age-appropriate movement.
- Garment-focused: hands away from the garment, one hand touching a sleeve/pocket, arms relaxed.

Background categories:

- Studio: pure white, warm off-white/beige, light gray, soft pastel, color-matched seamless, minimal editorial.
- Outdoor: quiet city street, clean pedestrian street, green park, garden/flower walkway, beach promenade, modern architectural wall/courtyard.
- Lifestyle: family-friendly café, bright home interior, playroom/kids' room, bookshop, minimal modern apartment. No adult nightlife or ambiguous private settings for minors.
- Seasonal: spring garden, autumn street, winter city, summer outdoor, festive but uncluttered.

Controls: framing (full body default, 3/4, waist-up, detail crop), lighting, composition, optional note, “show full garment” toggle.

### 8.4 Model prompt

- Model reference defines identity/age presentation; garment reference defines the exact garment.
- Preserve the model's recognizable visual identity from the authorized AI reference; keep the depiction natural and age-appropriate.
- Dress the model in the exact garment; preserve color, silhouette, print, trims.
- Use the selected pose, background, framing, lighting, composition.
- Keep anatomy natural, hands realistic, one person only.
- No text, logos, watermarks, extra people, accessories that obscure the garment.
- Age-safe rules:
  - 2y/7y: natural child proportions, calm playful commercial tone, no makeup.
  - 13y/18y: age-appropriate styling only.
  - Never sexualize; no adult fashion poses for children; keep the 18-year-old category clearly separate.
- The garment must be visually dominant and correctly fitted.

### 8.5 Routes and UI

- Extend `/admin/instagram-studio` with a mode tab «مدل و ژست» (`scenario=model_scene`).
- Same generate endpoint; additional fields: `model_slot`, `pose_key`, `background_key`, framing/lighting/composition.
- Validation: slot exists, is active, has an image; pose/background keys valid; age-appropriate combination.
- Reference paths: model image first, then the garment references. Prompt mapping must say which is which.
- History filter by scenario.
- Owner settings page for uploads.

Definition of done: a manager can pick a variant, a model slot, a pose, and a background, and generate a family-friendly model photo with the exact garment.

---

## 9. Phase 4 — Text-only

Goal: create a campaign image from a free-text brief with no product reference.

- New tab `scenario=text`.
- Required: `user_text` (min 10 chars).
- Optional: aspect ratio.
- Endpoint: `POST /v1/images/generations` (no references).
- Prompt template:

```text
Create an original commercial Instagram fashion campaign image based on this creative brief:
"{user_text}"

Produce a refined, realistic fashion photograph with intentional composition, natural materials,
professional lighting, and a clear visual focal point. Leave clean negative space if the composition
is intended to receive text later. Do not include brand logos, watermarks, fake product labels, or
garbled text. Do not add unsupported product claims or unrelated objects.
```

- No garment fidelity block.
- Same aspect ratios, quota, history, download, and delete.
- No new table or worker branch; the generator already supports the no-reference path.

Definition of done: a manager can type a seasonal campaign brief and get a usable 4:5 image.

---

## 10. Phase 5 — Lean polish

### 10.1 Generate variation

- On the result panel and history card, render a form with the original hidden inputs (product, variant, references, scene, aspect, note, logo) that posts to `/admin/instagram-studio/generate`.
- Creates a new generation row and job. No new route or table.

### 10.2 Cost / usage display

- Show daily remaining on the studio page.
- Show the active model and its approximate cost from `image_models.PROVIDERS` (`cost` field; Seedream is `$0.035`).
- Show provider/model on each history card.
- Optional small “today / this week / this month” count from `BackgroundJob` rows. No billing reconciliation.

### 10.3 Better Persian errors

- Map categories to actionable messages:
  - `auth` → key missing/invalid; check Settings.
  - `content_policy` → provider refused; simplify the brief or remove sensitive wording.
  - `rate_limit` → wait and retry.
  - `server` / `network` → provider/network issue; retry later.
  - `no_image` / `refusal` → provider returned text/no image; adjust the brief.
  - `no_op` / `source_echo` → provider echoed the source; try another scene/reference.
- Show the message in the result panel and history.
- Keep provider codes in the log, not in the main UI.

### 10.4 Favorites

- `is_favorite` already exists.
- `POST /admin/instagram-studio/history/{id}/favorite` toggles it (manager+).
- Star icon on result/history cards.
- History filter `?favorite=1`.

### 10.5 Quality checklist

Create `docs/instagram-quality-checklist.md`:

- Fixed evaluation set: light garment, dark garment, patterned garment, small-detail garment, multi-color product, front/back/detail references.
- Review criteria: exact color, silhouette, print/embroidery placement, trims, seams, fabric texture, composition, background, anatomy (model phase), text/watermark absence.
- Failure labels: wrong color, changed design/detail, missing detail, bad anatomy, poor composition, background issue, artifacts.
- Rule: review every output before publishing; AI generation is probabilistic.
- Track failures by prompt version so prompt changes can be compared.

### 10.6 Staff documentation

Create `docs/instagram-studio.md`:

- What each scene means and when to use it.
- How to pick the right color variant and reference photos.
- How to write a useful English brief.
- Daily limit and cost awareness.
- Why text/logos are added later, not generated.
- Privacy/safety: all model references are AI-generated; no real customer photos.
- Review-before-publish rule.

---

## 11. Out of scope

Do not build these unless the owner explicitly asks:

- Auto-publishing to Instagram, captions, hashtags, content calendar.
- Brand style profile / brand kit.
- Separate catalog vs campaign modes.
- Multi-image campaign sets (hero + detail + lifestyle).
- Human review/fidelity feedback taxonomy.
- Batch 2–4 outputs per request.
- Deterministic text overlays (logo only).
- Multi-color group shots.
- Color-name-only generation; variant images are required.
- Video/reels.
- Custom model training/fine-tuning.
- Real-person model references and consent workflows; all models are AI.
- A second provider key or separate provider configuration.
- A second worker loop.
- A preset database table; presets stay in code.
- A separate output table; one generation row = one image, status from the job.

---

## 12. Risks and assumptions

1. **Garment fidelity is best-effort.** Prompts and reference images improve it, but the provider may still change color, print, or trims. Human review is mandatory before publishing.
2. **One color per generation** reduces color mixing. Do not pass multiple colorways in one request.
3. **Shared provider/model.** Switching the try-on model in Settings also changes Instagram quality, cost, and size support. That is intentional.
4. **Exact custom sizes** work on Seedream per the docs, but a model/provider change may reject them. The crop fallback must be tested.
5. **UTC day boundary.** The app and existing try-on quota use UTC; the 20/day reset happens at UTC midnight, not Iran midnight. Keep it consistent unless the owner asks to change both.
6. **Scheduler race.** If `instagram_image` is not released by the scheduler, the generic 5-minute path may process it and bypass fail-fast semantics. The scheduler condition is part of Phase 1.
7. **Navigation tests.** Adding a sixth section intentionally breaks the current “five sections” assertion; update the test, not the feature.
8. **Packaging.** The frozen desktop build uses a hardcoded `hiddenimports` list; new modules must be added or the app may crash at runtime.
9. **No secrets in payloads.** Provider keys are resolved at execution time from Settings and never stored in job rows or browser responses.
10. **Path validation.** Selected reference paths must be checked against the variant gallery; never trust a posted filesystem path.
11. **Disk growth.** History is kept forever and files are not pruned. The owner can delete; monitor storage. If this becomes a problem, add retention in a later phase.
12. **SQLite/concurrency.** Reuse the existing worker and session patterns; do not add a new concurrency mechanism in Phase 1.

---

## 13. Suggested implementation order

1. `models.py` → `InstagramGeneration`.
2. `services/instagram_prompts.py` + tests.
3. `services/instagram_gen.py` + image normalization tests (fake provider).
4. `services/jobs.py` + `services/tryon_worker.py` + `services/scheduler.py` + worker tests.
5. `services/security.py` quota helpers.
6. `services/instagram_studio.py` validation/creation helpers.
7. `routers/instagram_studio.py` + templates + navigation + icon.
8. Settings limit field + template.
9. `store_management.spec` hidden imports.
10. Navigation test updates.
11. Full test run + manual smoke test with the real provider (one image).

For the live smoke test, use a product with a real image and generate one 4:5 packshot. Verify the output file, history row, prompt, and cost/limit display. Then try one intentionally bad request (missing variant image) and confirm the Persian error.

---

## 14. Commands

Run the app:

```bash
./start.sh
# or
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Run focused tests:

```bash
.venv/bin/python -m pytest tests/test_instagram_prompts.py tests/test_instagram_studio.py \
  tests/test_instagram_worker.py tests/test_instagram_images.py tests/test_navigation.py -q
```

Run try-on regression tests:

```bash
.venv/bin/python -m pytest tests/test_tryon_phase1.py -q
```

Build the desktop app (after updating `store_management.spec`):

```bash
./build_mac.sh
```

---

## 15. Source plans

- `instagram_studio_feature_plan.md` — prompt architecture, four modes, model library, safety, quality evaluation, delivery phases.
- `instagram-image-generation-plan.md` — codebase-specific routes, tables, scenario definitions, MVP order.

This document is the merged, decision-complete version. If a future session needs to change a locked decision, update section 2 first, then the affected phase.
