"""HTMX close-out — the last three fetch speakers go quiet: the product
form's silent demand +1, the try-on page's photo upload and live job strip,
and the barcode print tray's cross-page preview read. Each keeps its server
JSON contract byte-identical for callers that are not htmx — the phone, the
print flow and any old client depend on that wire.
"""
from models import BackgroundJob, Product, ProductVariant
from tests.conftest import csrf_token


def _variant(db_session, barcode="CLOSE-01"):
    product = Product(name="محصول بستن", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, barcode=barcode)
    db_session.add(variant)
    db_session.commit()
    return product, variant


# ── Product form: the silent demand +1 ──────────────────────────────────────

def test_demand_button_posts_through_htmx(authed, client, db_session):
    product, variant = _variant(db_session)
    page = client.get(f"/admin/products/{product.id}").text
    assert f'hx-post="/admin/variants/{variant.id}/demand' in page
    assert f"hx-target=\"[data-demand-cell='{variant.id}']\"" in page
    assert f'<td data-demand-cell="{variant.id}">' in page  # the real cell
    assert "data-demand-btn" not in page  # the fetch-day script is gone
    # The tally cell and the swapped fragment share one source.
    assert 'data-demand-cell' in page and '{% include' not in page


def test_demand_hx_answers_the_tally_cell(authed, client, db_session):
    product, variant = _variant(db_session)
    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    resp = client.post(f"/admin/variants/{variant.id}/demand",
                       data={"csrf_token": token},
                       headers={"HX-Request": "true"}, follow_redirects=False)
    body = resp.text.lstrip()
    assert body.startswith(f'<td data-demand-cell="{variant.id}">')
    assert "badge badge-info demand-updated" in body   # the raised flag
    assert "1 تقاضا" in body


def test_demand_xhr_json_contract_unchanged(authed, client, db_session):
    # Old callers spoke X-Requested-With fetch; the wire stays as it was.
    product, variant = _variant(db_session)
    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    resp = client.post(f"/admin/variants/{variant.id}/demand",
                       data={"csrf_token": token},
                       headers={"X-Requested-With": "XMLHttpRequest"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "demand_count": 1}


# ── Try-on: upload transport and the live job strip ─────────────────────────

def _job(db_session, status, **extra):
    job = BackgroundJob(job_type="tryon", payload="{}", status=status, **extra)
    db_session.add(job)
    db_session.commit()
    return job


def test_tryon_page_speaks_htmx_only(authed, client):
    page = client.get("/admin/try-on").text
    assert "htmx.min.js" in page
    assert "fetch(" not in page and "DOMParser" not in page
    assert "data-status-url" not in page     # the old poll's address carrier
    assert "htmx.ajax" in page               # the crop upload's transport


def test_job_htx_renders_the_armed_strip(authed, client, db_session):
    job = _job(db_session, "processing")
    resp = client.get(f"/api/image-gen/jobs/{job.id}", headers={"HX-Request": "true"})
    body = resp.text
    assert '<div id="tryon-live" role="status"' in body
    assert f'hx-get="/api/image-gen/jobs/{job.id}"' in body  # re-arms itself
    assert 'hx-target="this" hx-swap="outerHTML"' in body
    assert "در حال تولید… (processing)" in body


def test_job_htx_failed_strip_stops_asking(authed, client, db_session):
    job = _job(db_session, "failed", error_message="پرامپت نامناسب")
    body = client.get(f"/api/image-gen/jobs/{job.id}", headers={"HX-Request": "true"}).text
    assert "hx-get" not in body                              # unarmed — poll over
    assert "ناموفق بود — ورودی‌ها سر جایشان است" in body


def test_job_htx_completed_redirects_out_of_the_poll(authed, client, db_session):
    job = _job(db_session, "processing")   # bank as completed from the poll's view
    job.status = "completed"
    job.result_url = "/static/uploads/generated/x.png"
    db_session.commit()
    resp = client.get(f"/api/image-gen/jobs/{job.id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # The old flow reloaded its own page; HX-Redirect does that without a swap.
    assert resp.headers["HX-Redirect"] == "/admin/try-on"
    assert not resp.text.strip()


def test_job_json_contract_unchanged_for_other_callers(authed, client, db_session):
    job = _job(db_session, "pending")
    assert client.get(f"/api/image-gen/jobs/{job.id}").json() == {
        "job_id": job.id, "status": "pending", "image_url": None, "error": None}
    job.status = "completed"
    job.result_url = "/static/uploads/generated/x.png"
    db_session.commit()
    assert client.get(f"/api/image-gen/jobs/{job.id}").json() == {
        "job_id": job.id, "status": "completed",
        "image_url": "/static/uploads/generated/x.png", "error": None}


def test_job_404_is_still_404_when_htmx(authed, client):
    assert client.get("/api/image-gen/jobs/999999",
                      headers={"HX-Request": "true"}).status_code == 404


# ── Barcode print tray: cross-page previews through the htmx pool ───────────

def test_barcode_print_reads_other_pages_through_htmx(authed, client):
    page = client.get("/admin/barcodes/print").text
    assert "fetch(" not in page and "XMLHttpRequest" not in page
    assert "await htmx.ajax" in page
    assert "select: '.tag-selection-preview'" in page   # only previews cross over
    assert "tag-selection-preview" in page               # the gather is unchanged


def test_upload_json_contract_unchanged(authed, client):
    resp = client.post("/api/image-gen/upload-kid-photo",
                       files={"file": ("x.txt", b"plain", "text/plain")})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "فایل باید عکس باشد."
