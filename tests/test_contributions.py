import io

import pytesseract
import pytest
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from catalog.models import Product
from catalog.services.importer import import_off_product
from contributions.models import Contribution, ContributionImage, ModerationAudit, OCRRun
from contributions.services import drafts, moderation, ocr

pytestmark = pytest.mark.django_db


def jpeg(size=(400, 300), color=(200, 220, 210), exif=None) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    kwargs = {"exif": exif} if exif is not None else {}
    img.save(buf, format="JPEG", **kwargs)
    return buf.getvalue()


def upload(content: bytes, name="photo.jpg", ctype="image/jpeg"):
    return SimpleUploadedFile(name, content, content_type=ctype)


@pytest.fixture
def product(off_payload):
    return import_off_product(off_payload())


@pytest.fixture
def draft(product):
    return drafts.create_draft(session_key="s", problem_type="missing_data", product=product)


# ---------- Contribution flow (HTML) ----------


def test_full_flow_photo_check_submit(client, product, queued_tasks):
    response = client.post(
        reverse("contribution-start"), {"problem_type": "incorrect_data", "product": product.pk}
    )
    c = Contribution.objects.get()
    assert response.url == reverse("contribution", kwargs={"token": c.access_token})

    url = reverse("contribution", kwargs={"token": c.access_token})
    client.post(
        reverse("contribution-upload", args=[c.access_token]),
        {"photo": upload(jpeg()), "kind": "nutrition"},
    )
    assert c.images.count() == 1
    assert OCRRun.objects.filter(image__contribution=c).count() == 1
    assert queued_tasks[-1][0] == "contributions.tasks.run_ocr"

    r = client.post(
        url + "?step=check",
        {
            "name": "Fixed name",
            "nutrition_basis": "per_100g",
            "n_sugars": "3.5",
            "n_carbohydrates": "3.6",
        },
    )
    assert r.status_code == 302 and r.url.endswith("step=submit")
    c.refresh_from_db()
    assert c.proposed["name"] == "Fixed name"
    assert c.status == "draft"

    r = client.post(url + "?step=submit", {})
    assert r.status_code == 200  # consent missing
    c.refresh_from_db()
    assert c.status == "draft"
    client.post(url + "?step=submit", {"consent": "on"})
    c.refresh_from_db()
    assert c.status == "submitted" and c.consent_publication and c.consent_text
    product.refresh_from_db()
    assert product.name == "Greek style yogurt"  # nothing public before moderation


def test_check_step_rejects_inconsistent_values(client, draft):
    url = reverse("contribution", kwargs={"token": draft.access_token})
    r = client.post(
        url + "?step=check",
        {"nutrition_basis": "per_100g", "n_sugars": "20", "n_carbohydrates": "5"},
    )
    assert r.status_code == 200
    assert b"cannot exceed" in r.content
    draft.refresh_from_db()
    assert draft.proposed == {}


def test_manual_entry_works_without_any_photo_or_ocr(client, draft):
    url = reverse("contribution", kwargs={"token": draft.access_token})
    client.post(url + "?step=check", {"brand": "New brand"})
    client.post(url + "?step=submit", {"consent": "on"})
    draft.refresh_from_db()
    assert draft.status == "submitted" and not OCRRun.objects.exists()


def test_unknown_token_is_404_and_tokens_are_long(client, draft):
    assert len(draft.access_token) >= 40
    assert client.get(reverse("contribution", kwargs={"token": "nope"})).status_code == 404


# ---------- Uploads ----------


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "x.svg"),
        (b"just text", "x.jpg"),
        (b"", "empty.jpg"),
        (b"GIF89a" + b"\0" * 100, "x.gif"),
    ],
)
def test_invalid_uploads_are_rejected(draft, content, name):
    with pytest.raises(drafts.ImageRejected):
        drafts.add_image(draft, upload(content, name), "front")
    assert not ContributionImage.objects.exists()


def test_oversize_and_bomb_and_tiny(draft, settings):
    settings.UPLOAD_MAX_BYTES = 1000
    with pytest.raises(drafts.ImageRejected):
        drafts.add_image(draft, upload(jpeg((800, 800))), "front")
    settings.UPLOAD_MAX_BYTES = 10_000_000
    settings.UPLOAD_MAX_PIXELS = 100_000
    buf = io.BytesIO()
    Image.new("L", (1000, 1000)).save(buf, format="PNG")  # 1 MP > limit; compresses tiny
    with pytest.raises(drafts.ImageRejected):
        drafts.add_image(draft, upload(buf.getvalue(), "bomb.png", "image/png"), "front")
    settings.UPLOAD_MAX_PIXELS = 40_000_000
    with pytest.raises(drafts.ImageRejected):
        drafts.add_image(draft, upload(jpeg((50, 50))), "front")


def test_exif_and_gps_are_stripped(draft):
    exif = Image.Exif()
    exif[0x8825] = {2: (45.0, 30.0, 0.0)}  # GPSInfo
    exif[0x010F] = "CameraMaker"
    img = drafts.add_image(draft, upload(jpeg(exif=exif)), "front")
    with img.file.open("rb") as fh:
        stored = Image.open(fh)
        assert not stored.getexif()
        assert stored.format == "JPEG"


def test_duplicate_photo_rejected(draft):
    data = jpeg()
    drafts.add_image(draft, upload(data), "front")
    with pytest.raises(drafts.ImageRejected):
        drafts.add_image(draft, upload(data), "front")


def test_image_authorization(client, draft, product, admin_user):
    img = drafts.add_image(draft, upload(jpeg()), "front")
    own = reverse("contribution-image", args=[draft.access_token, img.pk])
    assert client.get(own).status_code == 200
    other = drafts.create_draft(session_key="x", problem_type="photos", product=product)
    assert (
        client.get(reverse("contribution-image", args=[other.access_token, img.pk])).status_code
        == 404
    )
    assert client.get(reverse("staff-image", args=[img.pk])).status_code == 302  # login required

    staff = User.objects.create_user("viewer", password="pw12345!", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("staff-image", args=[img.pk])).status_code == 403
    staff.user_permissions.add(Permission.objects.get(codename="view_contribution"))
    staff = User.objects.get(pk=staff.pk)
    client.force_login(staff)
    assert client.get(reverse("staff-image", args=[img.pk])).status_code == 200
    client.logout()

    # Not public before approval.
    from catalog.models import ProductImage

    assert not ProductImage.objects.filter(contribution_image=img).exists()
    drafts.submit(draft, consent=True)
    moderation.approve(draft.pk, admin_user, "ok")
    pub = ProductImage.objects.get(contribution_image=img)
    assert client.get(reverse("public-image", args=[pub.pk])).status_code == 200


def test_rejected_contribution_images_are_not_public(client, draft, admin_user):
    img = drafts.add_image(draft, upload(jpeg()), "front")
    drafts.submit(draft, consent=True)
    moderation.reject(draft.pk, admin_user, "blurry")
    img.refresh_from_db()
    assert not img.is_public


# ---------- OCR ----------


def make_run(draft):
    img = drafts.add_image(draft, upload(jpeg((600, 400))), "nutrition")
    return OCRRun.objects.get(image=img)


def test_ocr_timeout_marks_failed_and_manual_entry_still_possible(draft, monkeypatch):
    run = make_run(draft)

    def boom(*a, **k):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    assert ocr.run_ocr(run.pk) == "timeout"
    run.refresh_from_db()
    assert (run.status, run.error, run.attempts) == ("failed", "timeout", 1)
    assert drafts.save_proposal(draft, {"name": "Typed by hand"}, "").ok
    assert drafts.submit(draft, consent=True) == []


def test_ocr_engine_error_retried_once_automatically(draft, monkeypatch, queued_tasks, settings):
    settings.OCR_MAX_ATTEMPTS = 2
    run = make_run(draft)
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *a, **k: (_ for _ in ()).throw(pytesseract.TesseractError(1, "x")),
    )
    before = len(queued_tasks)
    assert ocr.run_ocr(run.pk) == "engine_error"
    run.refresh_from_db()
    assert run.status == "queued" and len(queued_tasks) == before + 1
    assert ocr.run_ocr(run.pk) == "engine_error"
    run.refresh_from_db()
    assert run.status == "failed" and run.attempts == 2


def test_ocr_job_is_idempotent(draft, monkeypatch):
    run = make_run(draft)
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *a, **k: {
            "text": ["Fat", "10", "g"],
            "conf": [90, 90, 90],
            "left": [0, 1, 2],
            "top": [0, 0, 0],
            "width": [1, 1, 1],
            "height": [1, 1, 1],
            "block_num": [1, 1, 1],
            "par_num": [1, 1, 1],
            "line_num": [1, 1, 1],
        },
    )
    assert ocr.run_ocr(run.pk) == "done"
    assert ocr.run_ocr(run.pk) == "skipped"
    run.refresh_from_db()
    assert run.text == "Fat 10 g" and run.parsed["proposals"][0]["nutrient"] == "fat"
    assert run.image.ocr_file  # derivative kept next to the original
    assert ocr.queue_ocr(run.image).pk == run.pk  # no duplicate run


# ---------- Moderation ----------


def submitted(product, proposed):
    c = drafts.create_draft(session_key="s", problem_type="incorrect_data", product=product)
    drafts.save_proposal(c, proposed, "")
    assert drafts.submit(c, consent=True) == []
    return c


def test_approve_publishes_and_locks(product, admin_user):
    c = submitted(product, {"brand": "Corrected"})
    out = moderation.approve(c.pk, admin_user, "checked label")
    product.refresh_from_db()
    assert out.status == "approved"
    assert product.brand == "Corrected" and "brand" in product.locked_fields
    assert product.current_revision.field_sources["brand"]["source"] == "contribution"
    assert ModerationAudit.objects.filter(contribution=c, action="approve").exists()


def test_conflict_when_base_changed_in_touched_field(product, admin_user):
    a = submitted(product, {"brand": "Brand A"})
    b = submitted(product, {"brand": "Brand B"})
    moderation.approve(a.pk, admin_user, "")
    out = moderation.approve(b.pk, admin_user, "")
    assert out.status == "conflict" and out.conflicting_fields == ("brand",)
    product.refresh_from_db()
    assert product.brand == "Brand A"  # no last-write-wins
    b.refresh_from_db()
    assert b.status == "conflict"
    with pytest.raises(moderation.ModerationError):
        moderation.rebase(b.pk, admin_user, "")
    moderation.rebase(b.pk, admin_user, "B is the newer label")
    assert moderation.approve(b.pk, admin_user, "").status == "approved"


def test_no_conflict_when_other_field_changed(product, admin_user):
    a = submitted(product, {"brand": "Brand A"})
    b = submitted(product, {"quantity": "450 g"})
    moderation.approve(a.pk, admin_user, "")
    assert moderation.approve(b.pk, admin_user, "").status == "approved"
    product.refresh_from_db()
    assert (product.brand, product.quantity) == ("Brand A", "450 g")


def test_reject_requires_reason(product, admin_user):
    c = submitted(product, {"brand": "X"})
    with pytest.raises(moderation.ModerationError):
        moderation.reject(c.pk, admin_user, " ")
    moderation.reject(c.pk, admin_user, "Not readable")
    c.refresh_from_db()
    assert c.status == "rejected"


def test_new_product_contribution_creates_product(admin_user):
    c = drafts.create_draft(session_key="s", problem_type="new_product", code="5900000000008")
    drafts.save_proposal(c, {"name": "Local product", "brand": "Local"}, "")
    drafts.submit(c, consent=True)
    moderation.approve(c.pk, admin_user, "")
    p = Product.objects.get(name="Local product")
    assert p.identifiers.get().normalized == "05900000000008"
    assert p.off_sync_status == "local_only"


def test_moderation_view_permissions(client, product):
    c = submitted(product, {"brand": "X"})
    url = reverse("admin:contributions_contribution_moderate", args=[c.pk])
    viewer = User.objects.create_user("v", password="pw12345!", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_contribution"))
    client.force_login(viewer)
    assert client.get(url).status_code == 200
    assert client.post(url, {"decision": "approve", "reason": ""}).status_code == 403
    c.refresh_from_db()
    assert c.status == "submitted"

    mod = User.objects.create_user("m", password="pw12345!", is_staff=True)
    mod.user_permissions.add(
        *Permission.objects.filter(codename__in=["view_contribution", "moderate_contribution"])
    )
    client.force_login(mod)
    client.post(url, {"decision": "approve", "reason": "ok"})
    c.refresh_from_db()
    assert c.status == "approved"


# ---------- Deleting a product you added ----------
def _own_product(user, admin_user, code="5900000000015"):
    c = drafts.create_draft(session_key="s", problem_type="new_product", code=code, user=user)
    drafts.save_proposal(c, {"name": "My local bar", "brand": "Mine"}, "")
    drafts.submit(c, consent=True)
    moderation.approve(c.pk, admin_user, "")
    return Product.objects.get(name="My local bar"), c


def test_the_person_who_added_a_product_can_delete_it(client, member, admin_user):
    product, c = _own_product(member, admin_user)
    url = reverse("product", args=[product.pk])
    client.force_login(member)
    html = client.get(url).content.decode()
    assert "You added this product" in html and reverse("product-delete", args=[product.pk]) in html
    r = client.post(reverse("product-delete", args=[product.pk]))
    assert r.status_code == 302 and r["Location"] == reverse("saved")
    assert not Product.objects.filter(pk=product.pk).exists()
    c.refresh_from_db()
    assert (
        c.product is None
        and ModerationAudit.objects.filter(contribution=c, action="product_deleted").exists()
    )


def test_others_cannot_delete_and_open_food_facts_products_cannot_be_deleted(
    client, member, admin_user, product
):
    mine, _c = _own_product(member, admin_user)
    other = User.objects.create_user("other2", password="pw12345!long")
    client.force_login(other)
    assert (
        "You added this product"
        not in client.get(reverse("product", args=[mine.pk])).content.decode()
    )
    assert client.post(reverse("product-delete", args=[mine.pk])).status_code == 404
    client.force_login(member)
    assert client.post(reverse("product-delete", args=[product.pk])).status_code == 404  # OFF
    # Someone else's approved change: no longer theirs alone.
    fix = submitted(mine, {"brand": "Fixed"})
    fix.user = other
    fix.save()
    moderation.approve(fix.pk, admin_user, "")
    assert client.post(reverse("product-delete", args=[mine.pk])).status_code == 404
    assert Product.objects.filter(pk=mine.pk).exists()
