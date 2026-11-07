# Instagram Studio — Brand Backgrounds & Consistent Scenes

Status: implemented 2026-11-07.
Date: 2026-11-07.
Use this file as the handoff brief; verify the current code before editing.

## Goal

Make every generated Instagram image look like it came from the same physical
shop set, while still letting the owner choose between different room
backgrounds. Also fix the misleading output-size wording in prompts.

The user wants:

- The Farsi label «جاکرکش» renamed to «رگال».
- The hanger scene to always use the same hanger, rail, and camera angle.
- The room/wall/floor/lighting to be selectable from detailed background presets.
- Background presets to be per-shop, so another shop does not get the same rooms.
- The shop default background preselected before generation.
- A prompt preview before generation (already implemented; keep it working).
- GPT image requests never to be told they receive `1440x1800`.

## Locked decisions

| Decision | Choice |
|---|---|
| Tabs with brand backgrounds | Product tabs + model tab |
| Background storage | Per-shop DB rows, editable in Settings |
| Background type | Text only, no reference image |
| Starter pack | 4 neutral studio sets, seeded per shop, editable |
| Default | One global shop default |
| Product scenes using backgrounds | Room-based only: `hanger`, `wooden_hanger_wall`, `folded_stack` |
| Free campaign (`product_brief` with empty scene) | Background applies |
| Hanger style | Fixed natural oak hanger + brushed-brass hook + slim matte-black rail |
| Hanger room | Selected background preset |
| Global brand-look block | No; each background carries lighting/lens/color grade |
| Model generic backgrounds | Keep all 21 and add brand backgrounds above them |
| Model lighting with brand background | Background wins; hide/disable lighting select |
| Background UI | Select + one-line description |
| Background editing | Owner only |
| Starter uniqueness | Accepted: identical until each shop edits them |
| Per-shop isolation | Each installation has its own DB; custom rows never ship |

## 1. Size correction (important)

Current code:

- `services/instagram_gen.py::_build_request`
  - `GENERATION_JSON` (text-to-image): uses `model_entry["sizes"][aspect]`.
  - `SEEDREAM_JSON`: uses `ASPECTS[aspect]["seedream_size"]`.
  - `MULTIPART` (GPT/GapGPT edit): uses `ASPECTS[aspect]["gpt_size"]`.
- `ASPECTS` in `services/instagram_prompts.py`:
  - `4:5`: `seedream_size=1440x1800`, `gpt_size=1024x1536`, `target=(1440,1800)`
  - `1:1`: `seedream_size=1440x1440`, `gpt_size=1024x1024`, `target=(1440,1440)`
  - `9:16`: `seedream_size=1440x2560`, `gpt_size=1024x1536`, `target=(1440,2560)`

So:

- `gpt-image-2.5-sunburst` / `flare` never receive `1440x1800`.
- They receive `1024x1536` for `4:5` and `9:16`, and `1024x1024` for `1:1`.
- `1440x1800` is only sent to Seedream.
- `normalize_image()` center-crops/resizes the provider output to the exact
  final size in `target`.

Do not change GPT requests to `1440x1800` unless the provider documents support.
The plan doc already says GPT common sizes are `1024x1024`, `1024x1536`, and
`1536x1024`. A `1440x1800` GPT request would be an undocumented paid failure,
and the current `MULTIPART` path has no size-retry fallback.

Prompt fix:

- Remove every `_section("اندازه خروجی", f"Output size: {aspect['seedream_size']} ...")`
  from prompt builders. The model only needs the aspect/composition clause.
- Keep `seedream_size` only as the Seedream API request size.
- Add a code comment near `ASPECTS` explaining API size vs final target.
- Add a test proving a MULTIPART request uses `gpt_size`, never `seedream_size`.

## 2. Visual model: scene + background + aspect

Keep three layers separate:

1. **Scene** — subject staging and fixed physical props:
   - hanger / rail / folded surface / packshot / ghost mannequin / details.
2. **Background** — the room shell:
   - wall, floor, lighting direction, lens, color grade, set consistency.
3. **Aspect** — `4:5`, `1:1`, `9:16` composition and final crop.

Rules:

- The hanger/rail belongs to the scene and must be identical every time.
- The wall/floor/lighting/lens/color grade belongs to the background preset.
- Camera angle/framing stays with the scene or the model framing controls.
- Do not put a hanger or rail inside the background prompt; that would conflict
  with folded-stack and model shots.

## 3. New data model

Add to `models.py`:

```python
class InstagramBackground(Base):
    __tablename__ = "instagram_backgrounds"

    id = Column(Integer, primary_key=True)
    key = Column(String(40), unique=True, nullable=False, index=True)
    label_fa = Column(String(120), nullable=False)
    description_fa = Column(String(240), nullable=True)
    prompt_en = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_default = Column(Boolean, nullable=False, default=False, index=True)
    is_starter = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    updated_by_staff_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
```

Notes:

- `key` is stable and is stored in the existing
  `InstagramGeneration.background_key` column. No new generation column needed.
- `Base.metadata.create_all` in `main.py` creates the new table on startup.
  Seed lazily, like `services/shop_models.py::ensure_shop_models`.
- Exactly one row should have `is_default=True`.
- No image/reference field: backgrounds are text only.

## 4. Background service

Create `services/instagram_backgrounds.py` with responsibilities:

- `STARTER_BACKGROUNDS`: the 4 starter dicts (see section 5).
- `ensure_instagram_backgrounds(db)`:
  - If the table is empty, insert all 4 starters.
  - If no active default exists, set the first active row as default.
  - Commit and return rows.
- `list_backgrounds(db, active_only=True)`:
  - Ensure seeded; return default first, then `sort_order`, then id.
- `get_background(db, key)` and `default_background(db)`.
- `save_background(...)`: create/update; validate label/prompt; one default only.
- `set_default(...)`, `toggle_active(...)`, `duplicate_background(...)`.
- `background_choices_for_scenario(db, scenario)`:
  - Product scenarios: brand rows only.
  - Model scene: brand rows first, then the 21 generic `BACKGROUNDS`.
- `resolve_generation_background(db, *, scenario, scene_key, background_key)`:
  - Returns a dict `{key, label, clause, source}` or `None`.
  - `source` is `"brand"` or `"generic"`.
  - Applies the scene applicability rules in section 7.
  - Falls back to the shop default when the key is missing/deactivated.
- `model_lighting_is_from_background(background)` helper or equivalent.

Key rules:

- If a brand background is deactivated and was the default, assign another
  active row as default in the same transaction.
- Do not hard-delete a background that old generations may reference; prefer
  deactivate. Old generations store their rendered prompt anyway.
- Validate `prompt_en` is non-empty and reasonably long (suggest >= 80 chars),
  label non-empty, and `description_fa` short enough for one line.

## 5. Starter backgrounds

Seed exactly these 4 rows per shop. They are editable templates, not final
brand identities. `key` values are stable.

### 5.1 `starter_ivory` — استودیو کرم

label_fa: `استودیو کرم`
description_fa: `دیوار کرم، کف چوب روشن، نور نرم پنجره`

prompt_en:

```text
Walls: warm ivory limewash plaster with a subtle hand-troweled texture.
Floor: pale oak with a matte finish.
Baseboard: slim matte-black baseboard.
Set piece: a low pale oak display table at the back of the set.
Lighting: soft north-facing window daylight from camera left; gentle natural shadows; no direct sun, no colored gels, no flash.
Lens and color: 50mm lens at f/4, natural perspective, no wide-angle distortion; warm neutral color grade, low contrast, true-to-life fabric colors, subtle fine grain.
Set rules: clean negative space; no other garments, furniture, props, text, logos, or watermarks.
```

### 5.2 `starter_white` — استودیو سفید

label_fa: `استودیو سفید`
description_fa: `دیوار و کف سفید یکدست، نور نرم استودیویی`

prompt_en:

```text
Walls: seamless pure-white studio wall with a smooth matte finish.
Floor: seamless white studio floor, clean and matte.
Baseboard: seamless white baseboard.
Set piece: a low white plinth at the back of the set.
Lighting: large softbox from camera left plus gentle fill from camera right; soft contact shadow; even high-key commercial lighting; no colored gels.
Lens and color: 50mm lens at f/4, natural perspective; neutral white balance, clean contrast, true-to-life fabric colors, subtle fine grain.
Set rules: no visible seams; no other garments, furniture, props, text, logos, or watermarks.
```

### 5.3 `starter_oak` — اتاق چوبی گرم

label_fa: `اتاق چوبی گرم`
description_fa: `دیوار گچی و پنل چوب گردو، نور گرم عصر`

prompt_en:

```text
Walls: warm off-white plaster with a wide natural-oak panel section.
Floor: warm oak planks with a matte finish.
Baseboard: natural oak baseboard.
Set piece: a low natural-oak display table at the back of the set.
Lighting: soft late-afternoon window light from camera right; warm gentle shadows; no direct sun, no flash.
Lens and color: 50mm lens at f/4, natural perspective; warm amber-neutral color grade, low contrast, true-to-life fabric colors, subtle fine grain.
Set rules: clean negative space; no other garments, furniture, props, text, logos, or watermarks.
```

### 5.4 `starter_gray` — استودیو خاکستری

label_fa: `استودیو خاکستری`
description_fa: `دیوار خاکستری روشن، کف بتنی، نور پخش`

prompt_en:

```text
Walls: light warm-gray seamless studio wall with a subtle matte texture.
Floor: pale gray concrete with a soft matte finish.
Baseboard: slim gray baseboard.
Set piece: a low gray plinth at the back of the set.
Lighting: diffused overhead daylight with gentle side falloff; soft natural shadows; no colored gels, no flash.
Lens and color: 50mm lens at f/4, natural perspective; cool-neutral color grade, low contrast, true-to-life fabric colors, subtle fine grain.
Set rules: clean negative space; no other garments, furniture, props, text, logos, or watermarks.
```

The first starter (`starter_ivory`) is the initial default.

## 6. Prompt assembly

### 6.1 Background lock

Every brand background prompt is prefixed with this fixed sentence:

```text
Use the shop's fixed studio set. Keep the wall, floor, lighting direction, lens, and color grade identical in every image; do not redesign, recolor, or rearrange the set.
```

Then append `prompt_en`. This is not a separate global brand-look block; it is
the consistency guard for the selected background.

### 6.2 New prompt section

For applicable scenarios, add a section:

- Persian label: `پس‌زمینه: {label_fa}`
- English text: lock sentence + `prompt_en`
- Kind: `SECTION_OPTION`

The prompt preview will automatically show it because it renders labeled
sections.

### 6.3 Scene changes

In `services/instagram_prompts.py`:

- Rename:
  - `SCENES["hanger"]["label"]`: `آویزان روی رگال`
  - `SCENES["wooden_hanger_wall"]["label"]`: `رگال چوبی روی دیوار`
- Rewrite `hanger` clause:

```text
The exact garment hangs on the shop's fixed signature hanger: a natural oak hanger with a centered brushed-brass hook, on a slim matte-black rail fixed to the wall. The hanger, rail, and camera angle must be identical in every image; do not change the hanger style, rail position, or angle. Centered, full garment visible, straight-on.
```

- Rewrite `wooden_hanger_wall` clause:

```text
The exact garment hangs on the same natural oak hanger with a centered brushed-brass hook, on the same slim matte-black rail, viewed closer against the wall with clean negative space. The hanger, rail, and camera angle must be identical in every image; do not change them.
```

- Rewrite `folded_stack` clause to defer the surface/room to the background:

```text
The exact garment is neatly folded on the set's clean display surface, edges aligned, boutique presentation, enough of the garment visible to identify it. Do not add props.
```

- Keep `packshot`, `ghost_mannequin`, `detail_closeup`, `fabric_texture`,
  `soft_shadow_studio`, and `back_view` as specialized scenes with their own
  backgrounds. They do not receive a background section.

Define:

```python
ROOM_BACKGROUND_SCENES = ("hanger", "wooden_hanger_wall", "folded_stack")
```

### 6.4 Prompt builder signatures

Keep existing `product_name` / `variant_color` parameters for caller
compatibility, but never write them into prompts (already implemented).

Add optional `background: dict | None = None` to:

- `garment_prompt_sections`
- `build_garment_prompt`
- `product_brief_prompt_sections`
- `build_product_brief_prompt`
- `model_prompt_sections`
- `build_model_prompt`

Rules:

- `garment_prompt_sections`: include background when
  `scene_key in ROOM_BACKGROUND_SCENES`.
- `product_brief_prompt_sections`: include background when `scene_key == ""`
  (free campaign) or `scene_key in ROOM_BACKGROUND_SCENES`.
- `model_prompt_sections`:
  - If `background["source"] == "brand"`: use its label and locked prompt;
    **do not add the separate `LIGHTINGS` section**.
  - If `background` is missing or source is `"generic"`: keep the existing
    `BACKGROUNDS[background_key]` + `LIGHTINGS[lighting_key]` behavior.
- Keep `background_key` parameters in the model prompt functions for backward
  compatibility. Prefer `background` when supplied.

### 6.5 Remove output-size claims

Remove `Output size: {seedream_size}` from all prompt section builders. Keep
the aspect composition clause. Do not tell the model a size that the provider
will not receive.

## 7. Scene applicability

| Scenario | Scene | Background section? |
|---|---|---|
| `garment` | `hanger` | yes |
| `garment` | `wooden_hanger_wall` | yes |
| `garment` | `folded_stack` | yes |
| `garment` | other scenes | no |
| `product_brief` | empty / free campaign | yes |
| `product_brief` | room-based | yes |
| `product_brief` | other scenes | no |
| `model_scene` | brand background | yes |
| `model_scene` | generic background | yes (existing location behavior) |
| `text` | — | no |
| `text_image` | — | no |

Server-side must enforce this; do not rely only on the UI hiding fields.

## 8. Studio UI

### 8.1 Context

In `routers/instagram_studio.py::_variant_card_context`:

- Add `backgrounds`:
  - Product scenarios: active brand backgrounds only.
  - Model scene: active brand backgrounds first, then the 21 generic
    `BACKGROUNDS` entries.
- Add `default_background_key`.
- Add `room_background_scenes` for the template/JS.
- Add `background_sources` map or put `data-source="brand|generic"` on each
  `<option>`.
- Add `background_descriptions` map or use data attributes.

`instagram_studio.html` must load the background JS for product/model
scenarios. Text scenarios do not need it.

### 8.2 Product forms

In `templates/partials/instagram_variant_card.html`:

- Add a `background_key` select in the product form.
- Show it only for room-based scenes and for `product_brief` with an empty
  scene.
- Use the shop default as `selected`.
- Show the selected background's `description_fa` under the select.
- When hidden, disable the select so it is not submitted.

### 8.3 Model form

- Replace the existing background select with brand + generic options.
- Brand options first; generic locations second.
- Each option carries `data-source`.
- Show the selected background's description under the select.
- When a brand background is selected, hide and disable the lighting field.
- When a generic location is selected, show the lighting field again.
- Server must ignore `lighting_key` for brand backgrounds even if posted.

### 8.4 JavaScript

Add `static/js/instagram_backgrounds.js`:

- Listen for `change` on the scene select:
  - Show/hide the background field for product scenes.
- Listen for `change` on the background select:
  - Update the description line.
  - For model scenes, show/hide the lighting field based on
    `option.dataset.source`.
- Handle HTMX swaps with `htmx.onLoad` or delegated `htmx:afterSwap`.
- On initial load, apply the correct state.

Keep it small and dependency-free; the app already uses vanilla JS.

### 8.5 Default

- The shop default is preselected in every applicable tab.
- The manager can override it for one generation.
- Only the owner changes the default in Settings.

## 9. Owner settings page

Add routes in `routers/admin.py`:

- `GET /admin/settings/instagram-backgrounds` — owner only.
- `POST /admin/settings/instagram-backgrounds/save` — create/update.
- `POST /admin/settings/instagram-backgrounds/default` — set default.
- `POST /admin/settings/instagram-backgrounds/toggle` — activate/deactivate.
- `POST /admin/settings/instagram-backgrounds/duplicate` — clone a starter
  or an existing row for editing.

Use `require_html_role(request, db, "owner")`, existing CSRF handling,
`log_action`, and `RedirectResponse` with `msg` / `err` like
`admin_shop_model_upload`.

Add a link in `templates/admin/settings.html` beside «مدل‌های فروشگاه»:

`پس‌زمینه‌های اینستاگرام`

Create `templates/admin/settings_instagram_backgrounds.html` following the
style of `settings_shop_models.html`:

- Intro explaining that backgrounds are raw English prompts and are per-shop.
- List of backgrounds:
  - label, description, default badge, starter badge, active state.
  - edit form or edit page.
  - set default button.
  - activate/deactivate button.
  - duplicate button.
- Add form:
  - Persian label (required)
  - Persian short description (required, one line)
  - English prompt textarea (required, `dir="ltr"`, min length)
  - active checkbox
  - default checkbox/radio
  - save button
- Help text with the lock sentence and an example prompt.
- A “duplicate starter” action so the owner can start from a known-good prompt.
- Warn on untouched starters: «این پس‌زمینه هنوز ویرایش نشده است».

Validation:

- At least one active background should remain.
- Exactly one default.
- Do not allow an empty label or prompt.
- If the default is deactivated, pick the next active row automatically.

## 10. Generation and persistence

In `services/instagram_studio.py`:

- `prompt_sections` accepts `background: dict | None`.
- `preview_prompt_sections` resolves the background from the posted
  `background_key` and passes it to `prompt_sections`.
- `create_generation` resolves and validates the background:
  - Product room-based/free campaign: brand background.
  - Model: brand or generic.
  - Specialized product scene: background ignored, store `None`.
- Store `background_key=background["key"]` on `InstagramGeneration` for all
  applicable scenarios.
- `variation_payload` already returns `background_key`; ensure a missing or
  deactivated key falls back to the shop default.
- Duplicate guard in `routers/instagram_studio.py`:
  - Add `InstagramGeneration.background_key == background_key` for product
    scenarios, not just model scenes.
- Keep `lighting_key` stored for model variations, but ignore it in the prompt
  when the selected background is a brand background.

## 11. Tests

Add/update tests:

### Prompt tests (`tests/test_instagram_prompts.py`)

- Hanger label is `آویزان روی رگال`.
- Hanger clause contains `natural oak hanger`, `matte-black rail`, and
  `identical in every image`.
- Background section appears for hanger / wooden_hanger_wall / folded_stack.
- Background section is absent for packshot / detail / ghost / fabric.
- Product brief with empty scene includes the background.
- Brand background prompt appears with the lock sentence.
- Model brand background suppresses the `LIGHTINGS` section.
- Model generic background still includes the lighting section.
- Prompts no longer contain `Output size: 1440x1800`.
- Existing no-product-name/color test still passes.

### Background service tests

- First access seeds exactly 4 starters.
- Exactly one default; first starter is the initial default.
- Saving an edited prompt persists.
- Setting a new default clears the old one.
- Deactivating the default assigns a new default.
- A missing/deactivated key falls back.

### Route tests (`tests/test_instagram_routes.py`)

- Settings backgrounds page is owner-only; manager gets 403.
- Owner can create/edit/duplicate/set-default/toggle.
- Studio page preselects the shop default.
- Product scene background appears only for room-based scenes.
- Model scene lists brand + generic backgrounds.
- Preview includes the background section and label.
- Generate stores `background_key` for product room-based/free campaign/model.
- Specialized product scene stores `None`.
- Duplicate guard includes background key.

### Image tests (`tests/test_instagram_images.py`)

- MULTIPART GPT request uses `gpt_size` for each aspect.
- It never sends `seedream_size`.
- Seedream still sends `seedream_size`.

### Theme/offline tests

- The new settings page renders in all themes.
- Any new static JS file is included in offline-asset checks if the project
  has such a test.

## 12. Docs

Update:

- `docs/instagram-studio.md`
  - Add a «پس‌زمینه‌های برند» section.
  - Explain owner editing, the shop default, room-based scenes, and the
    text-only similarity limitation.
  - Rename جاکرکش to رگال.
- `docs/instagram-studio-plan.md`
  - Add a new phase/notes for brand backgrounds and the size wording fix.

## 13. Implementation order

1. Prompt cleanup:
   - Rename labels.
   - Rewrite hanger/hanger-wall/folded clauses.
   - Remove output-size sections.
   - Add background section support to prompt builders.
2. Background data:
   - Add `InstagramBackground` model.
   - Add `services/instagram_backgrounds.py` with starters and CRUD helpers.
3. Settings:
   - Add owner routes.
   - Add template and settings link.
   - Add tests.
4. Studio:
   - Extend `_variant_card_context`.
   - Add product background select and model merged select.
   - Add JS for visibility and description.
5. Generation/preview:
   - Resolve background in preview and create_generation.
   - Store `background_key`.
   - Update duplicate guard and variation fallback.
6. Tests and docs.
7. Verify:
   - Run the Instagram/tryon test set.
   - Run the full suite if time allows.
   - Live provider smoke is still blocked by Avalai quota; do not wait on it.

## 14. Acceptance criteria

- A manager selecting «آویزان روی رگال» gets the same natural oak hanger,
  brushed-brass hook, and matte-black rail in every generation.
- The room/wall/floor/lighting come from the selected background preset and
  are identical every time that preset is used.
- The shop default background is preselected.
- The owner can edit the 4 starters, add new backgrounds, duplicate, deactivate,
  and set the default.
- Another shop's database has its own backgrounds; custom edits are not shared.
- Product backgrounds appear only for room-based scenes and free campaigns.
- Model brand backgrounds hide/disable lighting; generic locations keep it.
- GPT models receive `1024x1536` for 4:5, never `1440x1800`.
- Prompt preview shows the background section.
- Relevant tests pass.

## 15. Risks and limits

- Text-only backgrounds give strong similarity, not pixel-identical rooms.
  The fixed lock sentence and default preset are the main consistency tools.
- Untouched starter packs are identical between shops until edited.
- Raw English prompts require English competence; provide examples and
  duplicate-starter workflow.
- The model-scene lighting conditional must be enforced server-side too.
- Hard deletion of a used background can break variation; prefer deactivation.
- Live provider size behavior is still unverified for GPT beyond the documented
  common sizes; do not spend a paid request on `1440x1800`.
- No background reference images in this phase. That is a possible future
  upgrade if text-only similarity is not enough.

## 16. Current context / previous changes

The working tree already contains (uncommitted at the time of this plan):

- The «کمپین متنی با عکس» scenario using `/images/edits`.
- The prompt preview for every scenario.
- Removal of «محصول و رنگ» from the prompts.
- Prompt version `2026-11-07.2`.
- `InstagramGeneration.background_key` already exists.
- `InstagramGeneration` stores rendered prompts.

Do not re-implement those. Build the background feature on top of the current
tree and preserve the prompt preview behavior.
