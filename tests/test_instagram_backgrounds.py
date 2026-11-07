"""Per-shop Instagram background presets (no network)."""
from models import InstagramBackground
from services.instagram_prompts import BACKGROUNDS
from services.instagram_backgrounds import (
    DEFAULT_STARTER_KEY,
    STARTER_BACKGROUNDS,
    background_choices_for_scenario,
    default_background,
    duplicate_background,
    ensure_instagram_backgrounds,
    list_backgrounds,
    model_lighting_is_from_background,
    resolve_generation_background,
    save_background,
    set_default,
    toggle_active,
)
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff

LONG_PROMPT = (
    "Walls: warm ivory limewash plaster. Floor: pale oak. Lighting: soft window "
    "daylight from camera left. Lens: 50mm at f/4. No props, text, or logos."
)


def test_first_access_seeds_four_starters_and_one_default(db_session):
    rows = ensure_instagram_backgrounds(db_session)
    assert [row.key for row in rows] == [item["key"] for item in STARTER_BACKGROUNDS]
    assert all(row.is_starter for row in rows)
    assert all(row.is_active for row in rows)
    assert sum(row.is_default for row in rows) == 1
    assert default_background(db_session).key == DEFAULT_STARTER_KEY

    again = ensure_instagram_backgrounds(db_session)
    assert len(again) == 4


def test_saving_an_edited_starter_prompt_persists(db_session):
    ensure_instagram_backgrounds(db_session)
    save_background(
        db_session, key="starter_ivory", label_fa="استودیو کرم ویرایش‌شده",
        description_fa="دیوار کرم روشن", prompt_en=LONG_PROMPT, is_active=True)

    row = db_session.query(InstagramBackground).filter_by(key="starter_ivory").one()
    assert row.label_fa == "استودیو کرم ویرایش‌شده"
    assert row.description_fa == "دیوار کرم روشن"
    assert row.prompt_en == LONG_PROMPT
    assert row.is_starter is True
    assert row.is_default is True


def test_new_background_is_created_and_listed(db_session):
    ensure_instagram_backgrounds(db_session)
    row = save_background(
        db_session, key="brand_blue", label_fa="اتاق آبی",
        description_fa="دیوار آبی روشن", prompt_en=LONG_PROMPT)

    assert row.key == "brand_blue"
    assert row.is_starter is False
    assert row.is_default is False
    assert any(item.key == "brand_blue" for item in list_backgrounds(db_session))


def test_setting_a_new_default_clears_the_old_one(db_session):
    ensure_instagram_backgrounds(db_session)
    set_default(db_session, "starter_gray")

    defaults = (db_session.query(InstagramBackground)
                .filter(InstagramBackground.is_default.is_(True)).all())
    assert [row.key for row in defaults] == ["starter_gray"]


def test_deactivating_the_default_assigns_another_active_row(db_session):
    ensure_instagram_backgrounds(db_session)
    old_default = default_background(db_session)
    toggle_active(db_session, old_default.key)

    row = db_session.query(InstagramBackground).filter_by(key=old_default.key).one()
    assert row.is_active is False
    assert row.is_default is False
    current = default_background(db_session)
    assert current is not None
    assert current.is_active is True


def test_the_last_active_background_cannot_be_deactivated(db_session):
    import pytest

    ensure_instagram_backgrounds(db_session)
    for key in ("starter_ivory", "starter_white", "starter_oak"):
        toggle_active(db_session, key)

    with pytest.raises(ValueError):
        toggle_active(db_session, "starter_gray")
    assert default_background(db_session).key == "starter_gray"


def test_missing_or_deactivated_keys_fall_back_to_the_shop_default(db_session):
    ensure_instagram_backgrounds(db_session)
    fallback = default_background(db_session)

    resolved = resolve_generation_background(
        db_session, scenario="garment", scene_key="hanger",
        background_key="does_not_exist")
    assert resolved["key"] == fallback.key
    assert resolved["source"] == "brand"

    toggle_active(db_session, "starter_white")
    resolved = resolve_generation_background(
        db_session, scenario="garment", scene_key="folded_stack",
        background_key="starter_white")
    assert resolved["key"] == default_background(db_session).key


def test_resolution_applies_only_to_room_scenes_and_free_campaigns(db_session):
    ensure_instagram_backgrounds(db_session)
    default = default_background(db_session).key

    assert resolve_generation_background(
        db_session, scenario="garment", scene_key="packshot",
        background_key="starter_oak") is None
    assert resolve_generation_background(
        db_session, scenario="product_brief", scene_key="packshot",
        background_key="starter_oak") is None
    assert resolve_generation_background(
        db_session, scenario="product_brief", scene_key="",
        background_key="starter_oak")["key"] == "starter_oak"
    assert resolve_generation_background(
        db_session, scenario="garment", scene_key="hanger",
        background_key="starter_oak")["key"] == "starter_oak"
    assert resolve_generation_background(
        db_session, scenario="text", scene_key="", background_key="starter_oak") is None

    model = resolve_generation_background(
        db_session, scenario="model_scene", scene_key="", background_key="park")
    assert model["key"] == "park"
    assert model["source"] == "generic"

    assert default
    brand = resolve_generation_background(
        db_session, scenario="model_scene", scene_key="",
        background_key="starter_gray")
    assert brand["source"] == "brand"
    assert brand["key"] == "starter_gray"


def test_scenario_choices_split_brand_rows_from_generic_locations(db_session):
    product = background_choices_for_scenario(db_session, "garment")
    assert len(product) == 4
    assert {choice["source"] for choice in product} == {"brand"}

    model = background_choices_for_scenario(db_session, "model_scene")
    assert model[0]["source"] == "brand"
    assert any(choice["key"] == "park" and choice["source"] == "generic"
               for choice in model)
    assert len(model) == 4 + len(BACKGROUNDS)

    assert background_choices_for_scenario(db_session, "text") == []


def test_duplicate_clones_an_editable_non_default_copy(db_session):
    ensure_instagram_backgrounds(db_session)
    source = db_session.query(InstagramBackground).filter_by(key="starter_ivory").one()

    copy = duplicate_background(db_session, "starter_ivory")
    assert copy.key == "starter_ivory_copy"
    assert copy.label_fa == source.label_fa + " (کپی)"
    assert copy.prompt_en == source.prompt_en
    assert copy.is_starter is False
    assert copy.is_default is False
    assert copy.is_active is True

    second = duplicate_background(db_session, "starter_ivory")
    assert second.key == "starter_ivory_copy_2"


def test_save_rejects_empty_labels_and_short_prompts(db_session):
    import pytest

    with pytest.raises(ValueError):
        save_background(db_session, label_fa=" ", prompt_en=LONG_PROMPT)
    with pytest.raises(ValueError):
        save_background(db_session, label_fa="اتاق", prompt_en="too short")
    with pytest.raises(ValueError):
        save_background(db_session, key="Bad Key", label_fa="اتاق",
                        prompt_en=LONG_PROMPT)


def test_only_brand_backgrounds_own_the_model_lighting(db_session):
    ensure_instagram_backgrounds(db_session)
    brand = resolve_generation_background(
        db_session, scenario="model_scene", scene_key="",
        background_key="starter_ivory")
    generic = resolve_generation_background(
        db_session, scenario="model_scene", scene_key="", background_key="park")
    assert model_lighting_is_from_background(brand) is True
    assert model_lighting_is_from_background(generic) is False
    assert model_lighting_is_from_background(None) is False


def _login(client, db_session, role):
    user, password = _staff(db_session, f"backgrounds-{role}", role)
    _session_as(client, user, password)
    return user


def test_background_settings_page_is_owner_only(client, db_session):
    _login(client, db_session, "manager")
    assert client.get("/admin/settings/instagram-backgrounds",
                      follow_redirects=False).status_code == 403

    _login(client, db_session, "owner")
    page = client.get("/admin/settings/instagram-backgrounds")
    assert page.status_code == 200
    for item in STARTER_BACKGROUNDS:
        assert item["label_fa"] in page.text
    assert "این پس‌زمینه هنوز ویرایش نشده است" in page.text


def test_owner_can_edit_a_starter_background(client, db_session):
    _login(client, db_session, "owner")
    token = csrf_token(client, "/admin/settings/instagram-backgrounds")
    response = client.post(
        "/admin/settings/instagram-backgrounds/save",
        data={
            "csrf_token": token, "key": "starter_ivory",
            "label_fa": "استودیو کرم ویرایش‌شده",
            "description_fa": "دیوار کرم روشن",
            "prompt_en": LONG_PROMPT, "is_active": "1",
        }, follow_redirects=False)
    assert response.status_code == 303

    row = db_session.query(InstagramBackground).filter_by(key="starter_ivory").one()
    assert row.label_fa == "استودیو کرم ویرایش‌شده"
    assert row.prompt_en == LONG_PROMPT
    assert row.is_default is True


def test_owner_can_create_a_default_background(client, db_session):
    _login(client, db_session, "owner")
    token = csrf_token(client, "/admin/settings/instagram-backgrounds")
    response = client.post(
        "/admin/settings/instagram-backgrounds/save",
        data={
            "csrf_token": token, "label_fa": "اتاق آبی",
            "description_fa": "دیوار آبی روشن", "prompt_en": LONG_PROMPT,
            "is_active": "1", "is_default": "1",
        }, follow_redirects=False)
    assert response.status_code == 303

    db_session.expire_all()
    row = db_session.query(InstagramBackground).filter_by(label_fa="اتاق آبی").one()
    assert row.is_default is True
    assert row.is_active is True
    assert row.is_starter is False
    assert (db_session.query(InstagramBackground)
            .filter(InstagramBackground.is_default.is_(True)).count()) == 1


def test_owner_can_toggle_duplicate_and_fail_validation(client, db_session):
    _login(client, db_session, "owner")
    token = csrf_token(client, "/admin/settings/instagram-backgrounds")

    toggled = client.post(
        "/admin/settings/instagram-backgrounds/toggle",
        data={"csrf_token": token, "key": "starter_gray"}, follow_redirects=False)
    assert toggled.status_code == 303

    duplicated = client.post(
        "/admin/settings/instagram-backgrounds/duplicate",
        data={"csrf_token": token, "key": "starter_oak"}, follow_redirects=False)
    assert duplicated.status_code == 303
    assert db_session.query(InstagramBackground).filter_by(
        key="starter_oak_copy").count() == 1

    bad = client.post(
        "/admin/settings/instagram-backgrounds/save",
        data={"csrf_token": token, "key": "starter_ivory",
              "label_fa": "کوتاه", "prompt_en": "too short",
              "is_active": "1"}, follow_redirects=False)
    assert bad.status_code == 303
    assert "err=" in bad.headers["location"]
    assert (db_session.query(InstagramBackground)
            .filter_by(key="starter_ivory").one().prompt_en
            != "too short")


def test_settings_page_links_to_the_background_library(client, db_session):
    _login(client, db_session, "owner")
    page = client.get("/admin/settings").text
    assert "/admin/settings/instagram-backgrounds" in page
    assert "پس‌زمینه‌های اینستاگرام" in page
