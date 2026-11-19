import io
from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from catalog.models import ReviewFlag
from catalog.services.importer import import_off_product
from contributions.models import Contribution, ModerationAudit
from contributions.services import drafts
from myola.models import DiaryEntry, Note, ProductVisit
from staff.models import SiteSettings, StaffAction

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-password-1"


def make_user(email, *, staff=False, superuser=False, groups=(), perms=()):
    user = User.objects.create_user(
        username=email.split("@")[0], email=email, password=PASSWORD, is_staff=staff
    )
    if superuser:
        user.is_superuser = True
        user.save()
    for name in groups:
        user.groups.add(Group.objects.get(name=name))
    if perms:
        user.user_permissions.add(*Permission.objects.filter(codename__in=perms))
    return user


@pytest.fixture
def roles(db):
    call_command("setup_roles", stdout=io.StringIO())


@pytest.fixture
def superuser(db):
    return make_user("boss@example.com", staff=True, superuser=True)


@pytest.fixture
def boss(client, superuser):
    client.force_login(superuser)
    return client


@pytest.fixture
def product(off_payload):
    return import_off_product(off_payload())


def submitted(product, proposed):
    c = drafts.create_draft(session_key="s", problem_type="incorrect_data", product=product)
    drafts.save_proposal(c, proposed, "")
    assert drafts.submit(c, consent=True) == []
    return c


def data_conflict(product, off_payload):
    """Lock 'name' locally, then let OFF change it: one open data_conflict flag."""
    from catalog.services.revisions import publish_revision

    data = dict(product.current_revision.data, name="Corrected name")
    sources = dict(product.current_revision.field_sources, name={"source": "contribution"})
    publish_revision(product, data, source="contribution", field_sources=sources)
    product.locked_fields = ["name"]
    product.save()
    import_off_product(off_payload(product_name="OFF renamed it"))
    return ReviewFlag.objects.get(kind="data_conflict", field="name", status="open")


# ---------- Access control ----------

ALL_PAGES = ["dashboard", "contributions", "flags", "products", "users", "settings", "audit"]


@pytest.mark.parametrize("name", ALL_PAGES)
def test_anonymous_is_sent_to_login(client, name):
    r = client.get(reverse(f"staff:{name}"))
    assert r.status_code == 302
    assert r["Location"].startswith(reverse("account_login") + "?next=")


@pytest.mark.parametrize("name", ALL_PAGES)
def test_signed_in_non_staff_gets_403(member_client, name):
    assert member_client.get(reverse(f"staff:{name}")).status_code == 403


@pytest.mark.parametrize("name", ALL_PAGES)
def test_superuser_opens_everything(boss, name):
    assert boss.get(reverse(f"staff:{name}")).status_code == 200


def test_plain_staff_sees_dashboard_only(client):
    client.force_login(make_user("s@example.com", staff=True))
    assert client.get(reverse("staff:dashboard")).status_code == 200
    for name in ALL_PAGES[1:]:
        assert client.get(reverse(f"staff:{name}")).status_code == 403


def test_permissions_without_is_staff_are_not_enough(client, roles):
    client.force_login(make_user("x@example.com", groups=["Moderators"]))
    assert client.get(reverse("staff:contributions")).status_code == 403


def test_reviewer_reads_but_cannot_act(client, roles, product):
    c = submitted(product, {"brand": "X"})
    client.force_login(make_user("r@example.com", staff=True, groups=["Reviewers"]))
    for name in ("contributions", "flags", "products"):
        assert client.get(reverse(f"staff:{name}")).status_code == 200
    url = reverse("staff:contribution", args=[c.pk])
    r = client.get(url)
    assert r.status_code == 200 and b"do not have permission" in r.content
    assert client.post(url, {"decision": "approve"}).status_code == 403
    c.refresh_from_db()
    assert c.status == "submitted"
    for name in ("users", "settings", "audit"):
        assert client.get(reverse(f"staff:{name}")).status_code == 403


def test_moderator_can_moderate_but_not_manage_users_or_settings(client, roles, product):
    c = submitted(product, {"brand": "Moderated"})
    client.force_login(make_user("m@example.com", staff=True, groups=["Moderators"]))
    r = client.post(
        reverse("staff:contribution", args=[c.pk]), {"decision": "approve", "reason": "ok"}
    )
    assert r.status_code == 302
    c.refresh_from_db()
    assert c.status == "approved"
    for name in ("users", "settings"):
        assert client.get(reverse(f"staff:{name}")).status_code == 403
    assert client.post(reverse("staff:settings"), {"ai_model": "x"}).status_code == 403


def test_custom_permissions_open_users_and_settings(client):
    client.force_login(
        make_user("ops@example.com", staff=True, perms=["manage_users", "manage_settings"])
    )
    assert client.get(reverse("staff:users")).status_code == 200
    assert client.get(reverse("staff:settings")).status_code == 200
    assert client.get(reverse("staff:contributions")).status_code == 403


def test_navigation_only_lists_allowed_sections(client, roles):
    client.force_login(make_user("r@example.com", staff=True, groups=["Reviewers"]))
    html = client.get(reverse("staff:dashboard")).content.decode()
    assert reverse("staff:contributions") in html
    assert reverse("staff:users") not in html and reverse("staff:settings") not in html


# ---------- Dashboard ----------


def test_dashboard_counts(boss, superuser, product, off_payload, member):
    old = make_user("old@example.com")
    User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=30))
    submitted(product, {"brand": "A"})
    submitted(product, {"quantity": "1 kg"})
    data_conflict(product, off_payload)
    for _ in range(3):
        ProductVisit.objects.create(user=member, product=product, code="5941234567899")
    ProductVisit.objects.create(user=member, product=product, code="")  # opened, not scanned
    DiaryEntry.objects.create(
        user=member,
        date=timezone.localdate(),
        meal="lunch",
        name="Secret soup",
        amount=100,
        unit="g",
    )
    r = boss.get(reverse("staff:dashboard"))
    s = r.context["stats"]
    assert s["users_total"] == 3 and s["users_new_7d"] == 2
    assert s["contributions_waiting"] == 2
    assert s["flags_open"] == 1
    assert s["scans_total"] == 3 and s["scans"][-1].count == 3 and len(s["scans"]) == 7
    assert s["diary_total"] == 1
    assert s["off"].circuit_open is False
    assert b"Secret soup" not in r.content


def test_dashboard_ai_usage_is_optional(boss, monkeypatch):
    from staff import services

    monkeypatch.setattr(services, "ai_usage_this_month", lambda: None)
    r = boss.get(reverse("staff:dashboard"))
    assert r.status_code == 200 and "—" in r.content.decode()


def test_dashboard_shows_off_errors(boss):
    from catalog.models import LookupState

    LookupState.objects.create(off_code="1", last_error="timeout", last_attempt_at=timezone.now())
    LookupState.objects.create(off_code="2", last_error="not_found", last_attempt_at=timezone.now())
    off = boss.get(reverse("staff:dashboard")).context["stats"]["off"]
    assert off.errors_24h == 1 and off.errors_by_kind == [("timeout", 1)]


# ---------- Moderation ----------


def test_queue_filters(boss, product):
    a = submitted(product, {"brand": "A"})
    b = submitted(product, {"brand": "B"})
    Contribution.objects.filter(pk=b.pk).update(
        submitted_at=timezone.now() - timedelta(days=10), problem_type="missing_data"
    )
    url = reverse("staff:contributions")
    page = boss.get(url).context["page"]
    assert {c.pk for c in page} == {a.pk, b.pk}
    assert [c.pk for c in boss.get(url, {"age": "7"}).context["page"]] == [b.pk]
    assert [c.pk for c in boss.get(url, {"problem_type": "incorrect_data"}).context["page"]] == [
        a.pk
    ]
    assert list(boss.get(url, {"status": "approved"}).context["page"]) == []


def test_queue_htmx_returns_partial(boss, product):
    submitted(product, {"brand": "A"})
    r = boss.get(reverse("staff:contributions"), HTTP_HX_REQUEST="true")
    html = r.content.decode()
    assert r.status_code == 200 and "<table" in html and "staff-nav" not in html


def test_detail_shows_diff_and_audit(boss, product):
    c = submitted(product, {"brand": "Corrected brand"})
    r = boss.get(reverse("staff:contribution", args=[c.pk]))
    html = r.content.decode()
    assert "Corrected brand" in html and "Test Dairy" in html
    assert "Approve and publish" in html


def test_approve_through_panel(boss, superuser, product):
    c = submitted(product, {"brand": "Corrected"})
    r = boss.post(
        reverse("staff:contribution", args=[c.pk]), {"decision": "approve", "reason": "label ok"}
    )
    assert r.status_code == 302
    c.refresh_from_db()
    product.refresh_from_db()
    assert c.status == "approved" and c.moderator == superuser
    assert product.brand == "Corrected"
    audit = ModerationAudit.objects.get(contribution=c, action="approve")
    assert audit.actor == superuser and audit.reason == "label ok"


def test_reject_needs_reason_through_panel(boss, product):
    c = submitted(product, {"brand": "X"})
    url = reverse("staff:contribution", args=[c.pk])
    r = boss.post(url, {"decision": "reject", "reason": ""}, follow=True)
    assert b"reason is required" in r.content
    c.refresh_from_db()
    assert c.status == "submitted"
    boss.post(url, {"decision": "reject", "reason": "Blurry photo"})
    c.refresh_from_db()
    assert c.status == "rejected" and c.moderation_reason == "Blurry photo"
    assert ModerationAudit.objects.filter(contribution=c, action="reject").exists()


def test_conflict_then_rebase_through_panel(boss, product):
    a = submitted(product, {"brand": "Brand A"})
    b = submitted(product, {"brand": "Brand B"})
    boss.post(reverse("staff:contribution", args=[a.pk]), {"decision": "approve"})
    url = reverse("staff:contribution", args=[b.pk])
    r = boss.post(url, {"decision": "approve"}, follow=True)
    assert b"Nothing was published" in r.content
    b.refresh_from_db()
    assert b.status == "conflict"
    assert b"Re-base onto current revision" in r.content
    boss.post(url, {"decision": "rebase", "reason": "B is the newer label"})
    b.refresh_from_db()
    assert b.status == "submitted"
    assert ModerationAudit.objects.filter(contribution=b, action="rebase").exists()


# ---------- Review flags ----------


def test_flag_list_and_detail(boss, product, off_payload):
    flag = data_conflict(product, off_payload)
    r = boss.get(reverse("staff:flags"))
    assert [f.pk for f in r.context["page"]] == [flag.pk]
    html = boss.get(reverse("staff:flag", args=[flag.pk])).content.decode()
    assert "Corrected name" in html and "OFF renamed it" in html


def test_keep_local_value_closes_flag(boss, superuser, product, off_payload):
    flag = data_conflict(product, off_payload)
    boss.post(reverse("staff:flag", args=[flag.pk]), {"action": "keep"})
    flag.refresh_from_db()
    product.refresh_from_db()
    assert flag.status == "resolved" and flag.resolved_by == superuser
    assert product.name == "Corrected name" and "name" in product.locked_fields
    assert StaffAction.objects.get(action="flag_keep_local").actor == superuser


def test_accept_off_value_unlocks_and_publishes(boss, product, off_payload):
    flag = data_conflict(product, off_payload)
    before = product.current_revision_id
    boss.post(reverse("staff:flag", args=[flag.pk]), {"action": "accept_off"})
    flag.refresh_from_db()
    product.refresh_from_db()
    assert flag.status == "resolved"
    assert product.name == "OFF renamed it"
    assert "name" not in product.locked_fields
    assert product.current_revision_id != before
    assert product.current_revision.field_sources["name"]["source"] == "off"
    entry = StaffAction.objects.get(action="flag_accept_off")
    assert entry.details["old_value"] == "Corrected name"
    assert entry.details["new_value"] == "OFF renamed it"
    # Later OFF imports now update the field without raising a new conflict.
    import_off_product(off_payload(product_name="OFF again"))
    product.refresh_from_db()
    assert product.name == "OFF again"
    assert not ReviewFlag.objects.filter(status="open").exists()


def test_accept_off_refused_for_other_kinds(boss, product):
    from catalog.services.importer import raise_flag

    raise_flag(ReviewFlag.Kind.UNUSUAL_VALUE, product, "nutrition", {"warning": "x"})
    flag = ReviewFlag.objects.get()
    r = boss.post(reverse("staff:flag", args=[flag.pk]), {"action": "accept_off"}, follow=True)
    assert b"Only data conflicts" in r.content
    flag.refresh_from_db()
    assert flag.status == "open"
    boss.post(reverse("staff:flag", args=[flag.pk]), {"action": "close", "note": "fine"})
    flag.refresh_from_db()
    assert flag.status == "resolved"
    assert StaffAction.objects.get(action="flag_close").details["note"] == "fine"


def test_reviewer_cannot_resolve_flags(client, roles, product, off_payload):
    flag = data_conflict(product, off_payload)
    client.force_login(make_user("r@example.com", staff=True, groups=["Reviewers"]))
    assert client.post(reverse("staff:flag", args=[flag.pk]), {"action": "keep"}).status_code == 403
    flag.refresh_from_db()
    assert flag.status == "open"


# ---------- Products ----------


def test_product_search_and_detail(boss, product):
    r = boss.get(reverse("staff:products"), {"q": "yogurt"})
    assert product in list(r.context["page"])
    r = boss.get(reverse("staff:products"), {"q": product.off_code})
    assert product in list(r.context["page"])
    html = boss.get(reverse("staff:product", args=[product.pk])).content.decode()
    assert "r1" in html and reverse("product", args=[product.pk]) in html


def test_refresh_from_off_queues_task(boss, product, queued_tasks):
    r = boss.post(reverse("staff:product-refresh", args=[product.pk]))
    assert r.status_code == 302
    assert queued_tasks[-1][0] == "catalog.tasks.refresh_product"
    assert queued_tasks[-1][1][0] == str(product.pk)
    assert StaffAction.objects.filter(action="product_refresh", target=str(product.pk)).exists()


# ---------- Users ----------


def test_users_page_never_shows_personal_content(boss, member, product):
    DiaryEntry.objects.create(
        user=member,
        date=timezone.localdate(),
        meal="lunch",
        name="Secret soup",
        amount=100,
        unit="g",
    )
    Note.objects.create(user=member, text="My private note text")
    r = boss.get(reverse("staff:users"), {"q": "member"})
    html = r.content.decode()
    assert "member@example.com" in html
    assert "Secret soup" not in html and "My private note text" not in html
    row = next(u for u in r.context["page"] if u.pk == member.pk)
    assert (row.diary_count, row.notes_count, row.email_verified) == (1, 1, True)
    html = boss.get(reverse("staff:user-delete", args=[member.pk])).content.decode()
    assert "Secret soup" not in html and "My private note text" not in html


def test_deactivate_and_activate(boss, superuser, member):
    url = reverse("staff:user-action", args=[member.pk, "deactivate"])
    assert boss.post(url).status_code == 302
    member.refresh_from_db()
    assert not member.is_active
    r = boss.post(
        reverse("staff:user-action", args=[member.pk, "activate"]), HTTP_HX_REQUEST="true"
    )
    assert r.status_code == 200 and b"Account activated" in r.content and b"<tr" in r.content
    member.refresh_from_db()
    assert member.is_active
    actions = list(StaffAction.objects.order_by("created_at").values_list("action", "actor"))
    assert actions == [("user_deactivate", superuser.pk), ("user_activate", superuser.pk)]


def test_superusers_are_protected(boss, client):
    other = make_user("root@example.com", staff=True, superuser=True)
    boss.post(reverse("staff:user-action", args=[other.pk, "deactivate"]))
    other.refresh_from_db()
    assert other.is_active
    boss.post(reverse("staff:user-delete", args=[other.pk]), {"confirm": "root@example.com"})
    assert User.objects.filter(pk=other.pk).exists()
    assert not StaffAction.objects.exists()


def test_resend_confirmation(boss, superuser):
    user = make_user("new@example.com")
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=False)
    mail.outbox.clear()
    boss.post(reverse("staff:user-action", args=[user.pk, "resend"]))
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["new@example.com"]
    assert StaffAction.objects.get(action="user_resend_confirmation").target == "new@example.com"


def test_resend_refused_when_already_confirmed(boss, member):
    mail.outbox.clear()
    r = boss.post(reverse("staff:user-action", args=[member.pk, "resend"]), follow=True)
    assert b"already confirmed" in r.content and mail.outbox == []


def test_delete_requires_typed_email(boss, member):
    DiaryEntry.objects.create(
        user=member, date=timezone.localdate(), meal="lunch", name="x", amount=1, unit="g"
    )
    url = reverse("staff:user-delete", args=[member.pk])
    r = boss.post(url, {"confirm": "someone@else.com"})
    assert r.status_code == 200 and User.objects.filter(pk=member.pk).exists()
    r = boss.post(url, {"confirm": "Member@Example.com"})
    assert r.status_code == 302
    assert not User.objects.filter(pk=member.pk).exists()
    assert not DiaryEntry.objects.exists()
    entry = StaffAction.objects.get(action="user_delete")
    assert entry.target == "member@example.com"


# ---------- Settings and audit ----------


def test_settings_save_and_log(boss, superuser):
    r = boss.post(
        reverse("staff:settings"),
        {
            "ai_enabled": "on",
            "ai_provider": "openai",
            "ai_model": " gpt-test ",
            "ai_daily_messages_per_user": "12",
            "ai_monthly_budget_usd": "55.50",
            "maintenance_message": "Back soon",
        },
    )
    assert r.status_code == 302
    s = SiteSettings.load()
    assert s.ai_enabled and s.ai_provider == "openai" and s.ai_model == "gpt-test"
    assert s.ai_daily_messages_per_user == 12 and str(s.ai_monthly_budget_usd) == "55.50"
    assert s.updated_by == superuser
    entry = StaffAction.objects.get(action="settings_update")
    assert entry.details["changes"]["ai_provider"] == ["anthropic", "openai"]


def test_settings_rejects_invalid_values(boss):
    r = boss.post(
        reverse("staff:settings"),
        {
            "ai_provider": "nope",
            "ai_model": "m",
            "ai_daily_messages_per_user": "5000",
            "ai_monthly_budget_usd": "-1",
        },
    )
    assert r.status_code == 200 and r.context["form"].errors
    assert not StaffAction.objects.exists()


def test_audit_page_lists_staff_and_moderation_actions(boss, superuser, product, member):
    c = submitted(product, {"brand": "Z"})
    boss.post(reverse("staff:contribution", args=[c.pk]), {"decision": "reject", "reason": "no"})
    boss.post(reverse("staff:user-action", args=[member.pk, "deactivate"]))
    r = boss.get(reverse("staff:audit"))
    html = r.content.decode()
    assert "user_deactivate" in html and "member@example.com" in html
    assert r.context["moderation"][0].action == "reject"
    r = boss.get(reverse("staff:audit"), {"action": "nothing"})
    assert list(r.context["page"]) == []
