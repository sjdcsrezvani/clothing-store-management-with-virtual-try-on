"""The phone's HTTP endpoints, on their own listener (:8101).

These are the exact paths, headers and JSON shapes the existing APK speaks —
``GET /api/v1/device/poll``, ``POST /api/v1/device/heartbeat``,
``POST /api/v1/device/sms/{id}/result`` and ``.../delivery`` — served by the
store app itself instead of a rented VPS. The auth is the phone's own key
(``X-Device-API-Key``), verified against the store's ``SmsDevice`` row, so the
admin session/CSRF world on :8100 is never involved: the port can be opened to
the LAN (or forwarded) without exposing a single admin page.

The tiny app here is *mounted into* the main ASGI server too (tests and the
browser preview reach it without a second process); in production
``desktop_entry`` runs it as a real second uvicorn listener on :8101.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.sms_gateway import (
    authenticate_device,
    heartbeat as gateway_heartbeat,
    poll as gateway_poll,
    report_delivery,
    report_result,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/device")


class HeartbeatRequest(BaseModel):
    status: str = "online"
    battery_level: int | None = None
    signal_strength: int | None = None
    app_version: str | None = None


class SmsResultPayload(BaseModel):
    success: bool
    error: str | None = None


class DeliveryReportPayload(BaseModel):
    success: bool


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "Device API key required"}, status_code=401)


def _device(db: Session, request: Request):
    return authenticate_device(db, request.headers.get("X-Device-API-Key"))


@router.get("/poll")
async def poll(request: Request, db: Session = Depends(get_db)):
    """The APK claims waiting messages. One claim, one handout."""
    device = _device(db, request)
    if device is None:
        return _unauthorized()
    try:
        claimed = gateway_poll(db, device)
        db.commit()
    except Exception:                       # a busy sale must never read as a device error
        db.rollback()
        logger.exception("sms device poll failed")
        return JSONResponse({"sms_list": []}, status_code=503)
    return {"sms_list": claimed}


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatRequest, request: Request,
                    db: Session = Depends(get_db)):
    device = _device(db, request)
    if device is None:
        return _unauthorized()
    gateway_heartbeat(db, device, status=payload.status,
                      battery_level=payload.battery_level,
                      signal_strength=payload.signal_strength,
                      app_version=payload.app_version)
    db.commit()
    return {"status": "ok"}


@router.post("/sms/{sms_id}/result")
async def sms_result(sms_id: int, payload: SmsResultPayload, request: Request,
                     db: Session = Depends(get_db)):
    device = _device(db, request)
    if device is None:
        return _unauthorized()
    ok = report_result(db, device, sms_id, success=payload.success, error=payload.error)
    db.commit()
    if not ok:
        return JSONResponse({"detail": "SMS not found or not claimed"}, status_code=404)
    return {"status": "ok"}


@router.post("/sms/{sms_id}/delivery")
async def sms_delivery(sms_id: int, payload: DeliveryReportPayload, request: Request,
                       db: Session = Depends(get_db)):
    device = _device(db, request)
    if device is None:
        return _unauthorized()
    ok = report_delivery(db, device, sms_id, success=payload.success)
    db.commit()
    if not ok:
        return JSONResponse({"detail": "Can only report delivery for sent SMS"}, status_code=400)
    return {"status": "ok"}


# The standalone gateway app desktop_entry supervises on :8101.
gateway_app = FastAPI(title="SMS Device Gateway", docs_url=None, redoc_url=None,
                      openapi_url=None)
gateway_app.include_router(router)


@gateway_app.get("/health")
async def gateway_health():
    return {"status": "healthy", "service": "sms-gateway"}
