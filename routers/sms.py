"""پیامک: the dedicated page for every message the shop sends.

The patterns and the gateway credentials used to be buried in the middle of the
general settings form, and nothing anywhere recorded what was actually sent.
This router owns four pages instead:

* ``/admin/sms`` — the manager: the templates, their state, the gateway, and an
  honest count of what has gone out.
* ``/admin/sms/templates/...`` — the editor, with the variables and a live
  preview, whatever the template's kind.
* ``/admin/sms/send`` — a manual blast to a real audience, with the count shown
  before anything is queued.
* ``/admin/sms/history`` — the log: what was sent, to whom, and what the queue
  said about it.

Everything substantive lives in :mod:`services.sms_templates` and
:mod:`services.sms_send`; this file is only HTTP.
"""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Customer, Settings, SmsTemplate, to_english_digits
from services._common import fmt, jalali_str
from services.security import log_action, require_html_role
from services.sms import device_status_label, queue_sms
from services.sms_gateway import (
    GATEWAY_PORT,
    device_health,
    pairing_qr_data_uri,
    queue_snapshot,
    unpair_device,
)
from services.sms_send import (
    HISTORY_ORDERS,
    MODE_LABELS,
    audience_choices,
    message_filtered,
    message_overview,
    parse_phone_list,
    plan_from_form,
    preview_body,
    send_bulk,
    template_card,
)
from services.sms_triggers import SETTING_AUTO_SEND_LIMIT
from services.sms_templates import (
    CUSTOMER_SOURCES,
    CUSTOM_TRIGGER,
    CUSTOM_VARIABLES,
    DEFAULT_FOLLOW_UP_DAYS,
    # «خودکار/دستی» for a template — not to be confused with the audience
    # MODE_LABELS imported above, which name how a *blast* picks its people.
    MODE_LABELS as TEMPLATE_MODE_LABELS,
    SOURCE_FIELDS,
    SOURCE_LABELS,
    STATUS_LABELS,
    TRIGGERS,
    create_custom,
    customer_for_phone,
    delete_blocked_reason,
    duplicate,
    ensure_seeded,
    fire_summary,
    grouped_templates,
    send_info,
    sentences_for,
    sms_metrics,
    sync_to_settings,
    template_variables,
    trigger_from_form,
    unfilled_in_body,
    unfilled_tokens,
    validate,
    values_for_customer,
)
from services.templating import templates

router = APIRouter(prefix="/admin")

GATEWAY_KEYS = ("sms_api_key", "sms_device_id", "campaign_sms_limit", SETTING_AUTO_SEND_LIMIT)

# A just-issued device key lives only for this long in the request cycle — long
# enough to render the pairing QR, never persisted anywhere it could be read back.
_PAIRING_FLASH_LIMIT_SECONDS = 60


def _guard(request, db):
    return require_html_role(request, db, "manager")


def _owner_guard(request, db):
    return require_html_role(request, db, "owner")


def _settings_map(db: Session) -> dict:
    return {row.key: row.value for row in db.query(Settings).filter(
        Settings.key.in_(GATEWAY_KEYS),
    ).all()}


def _sms_device(db: Session):
    return device_health(db)


def _save_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))


def get_template_by_id(db: Session, template_id: int) -> SmsTemplate | None:
    """Fetch one template row, seeding the built-ins first so it always exists."""
    ensure_seeded(db)
    return db.query(SmsTemplate).filter(SmsTemplate.id == template_id).first()


def _cards_for(groups) -> dict:
    return {template.id: template for group in groups for template in group["templates"]}


# ── manager ───────────────────────────────────────────────────────────────────

@router.get("/sms", response_class=HTMLResponse)
async def admin_sms(request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    groups = grouped_templates(db)
    device = _sms_device(db)
    return templates.TemplateResponse(request, "admin/sms.html", {
        "groups": groups,
        "overview": message_overview(db),
        "config": _settings_map(db),
        "balance": device_status_label(db),
        "device": device,
        "device_status": device_status_label(db),
        "gateway_queue": queue_snapshot(db),
        "pairing_key": None,
        "pairing_qr": None,
        "gateway_port": GATEWAY_PORT,
        "can_configure": guard.role == "owner",
        "cards": {template.id: template_card(db, template) for template in _cards_for(groups).values()},
        "send_info": send_info,
        "fire": fire_summary(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/sms/config", response_class=HTMLResponse)
async def admin_sms_config(
    request: Request,
    campaign_sms_limit: str = Form(""),
    trigger_sms_limit: str = Form(""),
    db: Session = Depends(get_db),
):
    """The send ceilings — owner only. The gateway's key and device id left
    with the VPS: pairing below issues a fresh key to *this* phone instead."""
    guard = _owner_guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    for field, key, label in (
        (campaign_sms_limit, "campaign_sms_limit", "سقف هر ارسال گروهی"),
        # Templates the owner gave a trigger can spend money with nobody
        # watching, so their pace is a setting rather than a constant.
        (trigger_sms_limit, SETTING_AUTO_SEND_LIMIT, "سقف ارسال خودکار در هر بررسی"),
    ):
        limit = field.strip()
        if not limit:
            continue
        try:
            value = int(to_english_digits(limit))
        except (TypeError, ValueError):
            value = 0
        if value < 1:
            return RedirectResponse(url=f"/admin/sms?err={label} باید عددی بزرگ‌تر از صفر باشد.",
                                    status_code=303)
        _save_setting(db, key, str(value))
    db.commit()
    log_action(db, "sms_config", "به‌روزرسانی تنظیمات درگاه پیامک",
               request=request, target_type="settings")
    return RedirectResponse(url="/admin/sms?msg=تنظیمات درگاه پیامک ذخیره شد.", status_code=303)


# ── the device gateway (درگاه) ───────────────────────────────────────────────

def _gateway_context(request, db, guard, *, pairing=None, message="", error="") -> dict:
    """Everything the درگاه card re-renders after a pair/unpair."""
    device = _sms_device(db)
    return {
        "device": device,
        "device_status": device_status_label(db),
        "gateway_queue": queue_snapshot(db),
        "pairing_key": pairing[0] if pairing else None,
        "pairing_qr": (pairing_qr_data_uri({"base_url": pairing[1], "api_key": pairing[0]})
                       if pairing else None),
        "gateway_port": GATEWAY_PORT,
        "msg": message,
        "err": error,
    }


@router.post("/sms/gateway/pair", response_class=HTMLResponse)
async def admin_sms_gateway_pair(request: Request, db: Session = Depends(get_db)):
    """Issue (or rotate) the phone's key and show its QR exactly once."""
    guard = _owner_guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    from services.sms_gateway import pair_device

    device, raw_key = pair_device(db)
    db.commit()
    log_action(db, "sms_gateway_pair", f"درگاه پیامک جفت شد (دستگاه «{device.name}»)",
               request=request, target_type="sms_device", target_id=device.id)

    groups = grouped_templates(db)
    context = _gateway_context(request, db, guard,
                               pairing=(raw_key, _lan_base_url(request)))
    context.update({
        "groups": groups,
        "overview": message_overview(db),
        "config": _settings_map(db),
        "balance": device_status_label(db),
        "can_configure": guard.role == "owner",
        "cards": {template.id: template_card(db, template) for template in _cards_for(groups).values()},
        "send_info": send_info,
        "fire": fire_summary(db),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })
    return templates.TemplateResponse(request, "admin/sms.html", context)


@router.post("/sms/gateway/unpair", response_class=HTMLResponse)
async def admin_sms_gateway_unpair(request: Request, db: Session = Depends(get_db)):
    guard = _owner_guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    unpair_device(db)
    db.commit()
    log_action(db, "sms_gateway_unpair", "اتصال گوشی درگاه پیامک قطع شد",
               request=request, target_type="sms_device")
    return RedirectResponse(url="/admin/sms?err=اتصال گوشی قطع شد؛ پیامک‌های در صف می‌مانند تا گوشی دوباره جفت شود.",
                            status_code=303)


def _lan_base_url(request: Request) -> str:
    """What the phone should dial. The desktop launcher binds 0.0.0.0 on :8101
    and prints the LAN IP; here we echo the request host with the gateway port.
    When served through the preview/tests the hostname is loopback — correct
    for that context, and the QR text is always editable on the phone anyway."""
    from services.sms_gateway import GATEWAY_PORT as port

    host = (request.url.hostname or "127.0.0.1").strip()
    # A LAN-hosted request already carries the machine's own address; loopback
    # names are swapped for the configured LAN IP the launcher computed.
    if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("192.0.2.1", 80))
            host = sock.getsockname()[0]
        finally:
            sock.close()
    return f"http://{host}:{port}"


# ── template editor ───────────────────────────────────────────────────────────

def _form_context(request, db, *, template, edit_mode, values, error="", message="",
                  draft_variables=None):
    """The editor, either as it stands or as a refused save left it.

    ``draft_variables`` are the bindings the form just posted. On a refusal the
    page has to describe the text that was *rejected* — its slots, its holes and
    its preview — because describing the stored text instead points the warning
    at slots the owner has already changed.
    """
    if draft_variables is not None:
        variables = draft_variables
        unfilled = unfilled_in_body(values.get("body") or "", variables)
    else:
        variables = (template_variables(template) if template is not None
                     else list(CUSTOM_VARIABLES))
        unfilled = unfilled_tokens(template)
    return templates.TemplateResponse(request, "admin/sms_template_form.html", {
        "template": template,
        "edit_mode": edit_mode,
        "values": values,
        "variables": variables,
        "sources": CUSTOMER_SOURCES,
        "sentences": sentences_for(template.category if template is not None else "custom"),
        "unfilled": unfilled,
        "triggers": TRIGGERS,
        "trigger_days_default": DEFAULT_FOLLOW_UP_DAYS,
        "template_mode_labels": TEMPLATE_MODE_LABELS,
        "CUSTOM_TRIGGER": CUSTOM_TRIGGER,
        "preview": preview_body(template) if template is not None else "",
        "metrics": sms_metrics(template.body or "") if template is not None else sms_metrics(""),
        "send_info": send_info,
        "error": error,
        "msg": message,
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
    })


def _values_from_form(name, body, is_active) -> dict:
    return {"name": name or "", "body": body or "", "is_active": bool(is_active)}


def _unbound_slot_error(body, variables, trigger_key) -> str:
    """Why this text cannot be saved yet, or "" when it can.

    A custom template has no sender to hand its slots a value, so a token the
    text uses but nothing fills is a hole on the customer's phone: « عزیز، عید
    مبارک». Every custom template is refused for it — hand-sent ones included —
    because the owner only ever sees a preview and the customer is who ends up
    reading the gap. Saving it anyway is what let a template look finished while
    every message it sent was missing a word.

    An automatic one is refused twice over, so its explanation says the other
    half out loud: nobody reads it at all before it leaves.
    """
    holes = unfilled_in_body(body, variables)
    if not holes:
        return ""
    names = "، ".join(f"%{item['token']}%" for item in holes)
    reason = (f"این متن از {names} استفاده می‌کند، اما به هیچ مقداری وصل نیست و "
              f"جای آن در پیامک خالی می‌ماند.")
    if trigger_key:
        reason += (" چون این قالب خودبه‌خود فرستاده می‌شود، کسی هم پیش از ارسال "
                   "آن را نمی‌بیند.")
    return (f"{reason} برای هر متغیرِ متن یک مقدار انتخاب کنید، یا خودِ متن را جای "
            f"متغیر بنویسید.")


def _variables_from_form(form, existing=None) -> list[dict]:
    """Custom templates let the owner name, sample and *bind* their own tokens.

    The binding is what a token is filled from at send time, so it has to survive
    a save: rebuilding the list from scratch used to reset every slot to «no
    source», which quietly dropped the customer's name out of a template the
    first time it was edited.
    """
    previous = {item["token"]: item for item in (existing or CUSTOM_VARIABLES)}
    out = []
    for item in CUSTOM_VARIABLES:
        token = item["token"]
        before = previous.get(token, item)
        label = str(form.get(f"label_{token}", "") or "").strip() or before.get("label") or item["label"]
        sample = str(form.get(f"sample_{token}", "") or "").strip() or before.get("sample") or item["sample"]
        # Absent field (an older form, a scripted post) keeps the binding;
        # present-but-empty is the owner choosing «no value» on purpose.
        chosen = form.get(f"source_{token}")
        field = (before.get("field") or "") if chosen is None else str(chosen).strip()
        out.append({"token": token, "label": label[:60], "sample": sample[:120],
                    "field": field if field in SOURCE_FIELDS else None})
    return out


@router.get("/sms/templates/new", response_class=HTMLResponse)
async def admin_sms_template_new(request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    return _form_context(
        request, db, template=None, edit_mode=False,
        values={"name": "", "body": "", "is_active": True},
    )


@router.post("/sms/templates/new", response_class=HTMLResponse)
async def admin_sms_template_create(
    request: Request,
    name: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    error = validate(name, body)
    if error:
        return _form_context(request, db, template=None, edit_mode=False,
                             values=_values_from_form(name, body, True), error=error)
    form = await request.form()
    trigger_key, trigger_value = trigger_from_form(
        str(form.get("trigger_key", "") or ""), form.get("trigger_days"))
    variables = _variables_from_form(form)
    problem = _unbound_slot_error(body, variables, trigger_key)
    if problem:
        return _form_context(request, db, template=None, edit_mode=False,
                             values=_values_from_form(name, body, True), error=problem,
                             draft_variables=variables)
    template = create_custom(db, name=name, body=body, variables=variables,
                             trigger_key=trigger_key, trigger_days=trigger_value)
    db.commit()
    log_action(db, "sms_template_create", f"قالب پیامک «{template.name}» ساخته شد",
               request=request, target_type="sms_template", target_id=template.id)
    return RedirectResponse(
        url=f"/admin/sms/templates/{template.id}/edit?msg=قالب ساخته شد — متن را ببینید و آزمایش کنید.",
        status_code=303,
    )


@router.get("/sms/templates/{template_id}/edit", response_class=HTMLResponse)
async def admin_sms_template_edit(template_id: int, request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    template = get_template_by_id(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="قالب پیامک یافت نشد")
    return _form_context(
        request, db, template=template, edit_mode=True,
        values={"name": template.name, "body": template.body or "",
                "is_active": bool(template.is_active)},
        message=request.query_params.get("msg", ""),
    )


@router.post("/sms/templates/{template_id}", response_class=HTMLResponse)
async def admin_sms_template_update(
    template_id: int,
    request: Request,
    name: str = Form(""),
    body: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    template = get_template_by_id(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="قالب پیامک یافت نشد")

    error = validate(name, body)
    if error:
        return _form_context(request, db, template=template, edit_mode=True,
                             values=_values_from_form(name, body, is_active == "on"),
                             error=error)

    if template.is_builtin:
        template.name = name.strip()
        template.body = body
        template.is_active = is_active == "on"
    else:
        form = await request.form()
        submitted = _variables_from_form(form, template_variables(template))
        # Only a custom template can fire on its own; the built-ins have their
        # own senders, so their trigger stays empty however the form is posted.
        trigger_key, trigger_value = trigger_from_form(
            str(form.get("trigger_key", "") or ""), form.get("trigger_days"))
        # Checked before anything is written, so a refused save leaves the stored
        # template exactly as it was rather than half-edited in the session.
        problem = _unbound_slot_error(body, submitted, trigger_key)
        if problem:
            return _form_context(request, db, template=template, edit_mode=True,
                                 values=_values_from_form(name, body, is_active == "on"),
                                 error=problem, draft_variables=submitted)
        template.name = name.strip()
        template.body = body
        template.is_active = True
        template.variables = json.dumps(submitted, ensure_ascii=False)
        template.trigger_key, template.trigger_days = trigger_key, trigger_value
    # The legacy settings row is the contract every sender already reads, so it
    # is written on save — «غیرفعال» lands there as an empty pattern.
    sync_to_settings(db, template)
    db.commit()
    log_action(db, "sms_template_update", f"قالب پیامک «{template.name}» ویرایش شد",
               request=request, target_type="sms_template", target_id=template.id)
    return RedirectResponse(
        url=f"/admin/sms/templates/{template.id}/edit?msg=متن پیامک ذخیره شد.", status_code=303,
    )


@router.post("/sms/templates/{template_id}/toggle", response_class=HTMLResponse)
async def admin_sms_template_toggle(template_id: int, request: Request, db: Session = Depends(get_db)):
    """Switch a template on or off. Off mirrors an empty legacy pattern."""
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    template = get_template_by_id(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="قالب پیامک یافت نشد")

    if not template.is_active and not (template.body or "").strip():
        return RedirectResponse(
            url="/admin/sms?err=این قالب متنی ندارد؛ اول متن را بنویسید.", status_code=303,
        )
    template.is_active = not template.is_active
    sync_to_settings(db, template)
    db.commit()
    state = "فعال" if template.is_active else "غیرفعال"
    log_action(db, "sms_template_toggle", f"قالب «{template.name}» {state} شد",
               request=request, target_type="sms_template", target_id=template.id)
    return RedirectResponse(url=f"/admin/sms?msg=قالب «{template.name}» {state} شد.", status_code=303)


@router.post("/sms/templates/{template_id}/duplicate", response_class=HTMLResponse)
async def admin_sms_template_duplicate(template_id: int, request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    template = get_template_by_id(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="قالب پیامک یافت نشد")
    copy = duplicate(db, template)
    db.commit()
    log_action(db, "sms_template_duplicate", f"قالب «{template.name}» کپی شد",
               request=request, target_type="sms_template", target_id=copy.id)
    return RedirectResponse(
        url=f"/admin/sms/templates/{copy.id}/edit?msg=یک کپی ساخته شد — نام و متن را دلخواه تغییر دهید.",
        status_code=303,
    )


@router.post("/sms/templates/{template_id}/delete", response_class=HTMLResponse)
async def admin_sms_template_delete(template_id: int, request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    template = get_template_by_id(db, template_id)
    if template is None:
        return RedirectResponse(url="/admin/sms", status_code=303)

    reason = delete_blocked_reason(db, template)
    if reason:
        return RedirectResponse(url=f"/admin/sms?err={reason}", status_code=303)

    name = template.name
    db.delete(template)
    db.commit()
    log_action(db, "sms_template_delete", f"قالب پیامک «{name}» حذف شد",
               request=request, target_type="sms_template", target_id=template_id)
    return RedirectResponse(url=f"/admin/sms?msg=قالب «{name}» حذف شد.", status_code=303)


@router.post("/sms/templates/{template_id}/test", response_class=HTMLResponse)
async def admin_sms_template_test(
    template_id: int,
    request: Request,
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    """Queue the template as it stands to one number, so the owner can see it."""
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    template = get_template_by_id(db, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="قالب پیامک یافت نشد")

    target = parse_phone_list(phone)
    if not target:
        return RedirectResponse(
            url=f"/admin/sms/templates/{template.id}/edit?err=شماره آزمایشی معتبر نیست (مثال: ۰۹۱۲۳۴۵۶۷۸۹).",
            status_code=303,
        )
    customer = customer_for_phone(db, target[0])
    body = preview_body(template, values_for_customer(customer, template))
    job = await queue_sms(body, target[0], {}, db, template=template, source="test",
                          customer=customer, body=body)
    if job is None:
        return RedirectResponse(
            url=f"/admin/sms/templates/{template.id}/edit?err=ارسال آزمایشی در صف قرار نگرفت.",
            status_code=303,
        )
    log_action(db, "sms_template_test", f"ارسال آزمایشی قالب «{template.name}»",
               request=request, target_type="sms_template", target_id=template.id)
    return RedirectResponse(
        url=(f"/admin/sms/templates/{template.id}/edit?msg="
             "پیامک آزمایشی در صف قرار گرفت — دقیقاً همان متنی که در پیش‌نمایش دیدید."),
        status_code=303,
    )


# ── manual send ───────────────────────────────────────────────────────────────

def _active_choices(db) -> list[dict]:
    return [
        {"id": template.id, "name": template.name}
        for group in grouped_templates(db)
        for template in group["templates"]
        if template.is_active
    ]


def _send_context(request, db, *, template, plan=None, body="", error="", message="",
                  picked=None, numbers="", audience="all", transactional=False):
    return templates.TemplateResponse(request, "admin/sms_send.html", {
        "template_choices": _active_choices(db),
        "template": template,
        # Whoever is holding the send button should know whether this template is
        # normally automatic, normally sent from another page, or hand-sent here.
        "send_info": send_info,
        "unfilled": unfilled_tokens(template),
        "choices": audience_choices(db, transactional=transactional),
        "customers": db.query(Customer).order_by(Customer.first_name.asc(), Customer.id.asc()).all(),
        "plan": plan,
        "body": body,
        "audience": audience,
        "picked": picked or [],
        "numbers": numbers,
        "transactional": transactional,
        "error": error,
        "msg": message,
        "fmt": fmt,
        "mode_labels": MODE_LABELS,
    })


def _template_or_default(db: Session, template_id) -> SmsTemplate | None:
    if template_id:
        template = get_template_by_id(db, template_id)
        if template is not None:
            return template
    from services.sms_templates import templates_for

    rows = templates_for(db)
    return next((row for row in rows if row.category == "custom"), rows[0] if rows else None)


@router.get("/sms/send", response_class=HTMLResponse)
async def admin_sms_send_form(request: Request, template_id: int = 0, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    template = _template_or_default(db, template_id)
    if template is None:
        return RedirectResponse(url="/admin/sms?err=قالب پیامک فعالی برای ارسال وجود ندارد.",
                                status_code=303)
    # The right panel shows the real message with its sample values, not an
    # empty bubble, so the template choice is visible before any preview click.
    return _send_context(request, db, template=template, body=preview_body(template),
                         message=request.query_params.get("msg", ""),
                         error=request.query_params.get("err", ""))


@router.post("/sms/send", response_class=HTMLResponse)
async def admin_sms_send(
    request: Request,
    template_id: int = Form(0),
    audience: str = Form("all"),
    picked: list[str] = Form([]),
    numbers: str = Form(""),
    action: str = Form("preview"),
    transactional: str = Form(""),
    db: Session = Depends(get_db),
):
    """Preview the blast, then send it — the same form, two steps.

    The preview re-resolves the audience on the spot, so the number the owner
    approves is the number that gets queued (and the cap is applied to both).
    """
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    template = get_template_by_id(db, template_id)
    if template is None:
        return RedirectResponse(url="/admin/sms/send?err=قالب پیامک پیدا نشد.", status_code=303)

    is_transactional = transactional == "on"
    plan = plan_from_form(db, audience=audience, picked=picked, numbers=numbers,
                          transactional=is_transactional)
    sample_customer = plan["recipients"][0]["customer"] if plan["recipients"] else None
    body = preview_body(template, values_for_customer(sample_customer, template))
    if not plan["recipients"]:
        return _send_context(request, db, template=template, plan=plan, body=body,
                             error="با این انتخاب کسی برای ارسال پیدا نشد.",
                             audience=audience, picked=picked, numbers=numbers,
                             transactional=is_transactional)

    if action != "send":
        return _send_context(request, db, template=template, plan=plan, body=body,
                             audience=audience, picked=picked, numbers=numbers,
                             transactional=is_transactional)

    summary = await send_bulk(db, template=template, plan=plan, source="manual",
                              employee_id=guard.id)
    message = f"{summary['queued']} پیامک در صف قرار گرفت."
    if summary["empty"]:
        message += f" {summary['empty']} نفر متن خالی داشتند و فرستاده نشدند."
    if plan["capped"]:
        message += f" (سقف هر ارسال {plan['limit']} پیامک است؛ بقیه به نوبت بعد ماندند.)"
    log_action(
        db, "sms_send",
        f"ارسال دستی قالب «{template.name}» برای {summary['queued']} نفر ({plan['mode_label']})",
        request=request, target_type="sms_template", target_id=template.id,
        after={"queued": summary["queued"], "matched": plan["matched"], "audience": plan["mode"]},
    )
    return RedirectResponse(url=f"/admin/sms/history?msg={message}", status_code=303)


# ── history ───────────────────────────────────────────────────────────────────

@router.get("/sms/history", response_class=HTMLResponse)
async def admin_sms_history(
    request: Request,
    search: str = "",
    status: str = "all",
    source: str = "all",
    order: str = "newest",
    page: int = 1,
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    listing = message_filtered(db, search=search, status=status, source=source,
                              order=order, page=page)
    return templates.TemplateResponse(request, "admin/sms_history.html", {
        **listing,
        "overview": message_overview(db),
        "orders": HISTORY_ORDERS,
        "status_filters": (("all", "همه وضعیت‌ها"),) + tuple(STATUS_LABELS.items()),
        "source_filters": (("all", "همه فرستنده‌ها"),) + tuple(SOURCE_LABELS.items()),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
    })
