"""Staff panel operations that are not already services elsewhere.

Every state-changing operation here writes a ``StaffAction`` audit entry.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from catalog.models import LookupState, Product, ReviewFlag
from catalog.services.revisions import publish_revision
from off.mapping import DATA_FIELDS
from staff.models import StaffAction


class StaffError(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal | date):
        return str(value)
    return value


def log(actor, action: str, target: str = "", **details: Any) -> StaffAction:
    return StaffAction.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        target=str(target)[:200],
        details={k: _jsonable(v) for k, v in details.items()},
    )


# ---------- Dashboard ----------


@dataclass
class DayCount:
    day: date
    count: int
    share: int = 0  # 0-100, relative to the busiest day (for <meter>)


@dataclass
class OffStatus:
    circuit_open: bool
    errors_24h: int
    errors_by_kind: list[tuple[str, int]] = field(default_factory=list)
    refresh_failed: int = 0
    last_sync: Any = None
    last_error_at: Any = None


def per_day(qs, days: int = 7) -> list[DayCount]:
    """Rows of ``qs`` per local calendar day (``created_at``), oldest first, zero-filled."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start, time.min))
    counts = dict(
        qs.filter(created_at__gte=since)
        .annotate(d=TruncDate("created_at"))
        .values_list("d")
        .annotate(n=Count("pk"))
        .order_by()
    )
    days_list = [start + timedelta(days=i) for i in range(days)]
    rows = [DayCount(d, counts.get(d, 0)) for d in days_list]
    peak = max((r.count for r in rows), default=0)
    for r in rows:
        r.share = round(100 * r.count / peak) if peak else 0
    return rows


def ai_usage_this_month() -> dict[str, Any] | None:
    """Messages and cost this month, or None while the AI app has no usage table."""
    try:
        model = apps.get_model("ai", "AIUsage")
    except (LookupError, ImportError):
        return None
    start = timezone.localdate().replace(day=1)
    try:
        with transaction.atomic():
            agg = model.objects.filter(day__gte=start).aggregate(
                messages=Sum("messages"), cost=Sum("cost_usd")
            )
    except DatabaseError:
        return None
    from staff.models import SiteSettings

    cost = agg["cost"] or Decimal("0")
    return {
        "messages": agg["messages"] or 0,
        "cost": cost,  # US dollars, as billed
        "cost_gbp": SiteSettings.load().to_gbp(cost),
    }


def off_status() -> OffStatus:
    from catalog.services.lookup import off_circuit_open

    since = timezone.now() - timedelta(hours=24)
    errors = (
        LookupState.objects.filter(last_attempt_at__gte=since)
        .exclude(last_error__in=["", "not_found"])
        .values_list("last_error")
        .annotate(n=Count("pk"))
        .order_by("-n")
    )
    errors_by_kind = list(errors)
    return OffStatus(
        circuit_open=off_circuit_open(),
        errors_24h=sum(n for _, n in errors_by_kind),
        errors_by_kind=errors_by_kind,
        refresh_failed=Product.objects.filter(
            off_sync_status=Product.SyncStatus.REFRESH_FAILED
        ).count(),
        last_sync=Product.objects.aggregate(m=Max("off_synced_at"))["m"],
        last_error_at=LookupState.objects.exclude(last_error__in=["", "not_found"]).aggregate(
            m=Max("last_attempt_at")
        )["m"],
    )


def dashboard_stats() -> dict[str, Any]:
    from contributions.models import Contribution
    from myola.models import DiaryEntry, ProductVisit
    from staff.models import SiteSettings

    User = get_user_model()
    week_ago = timezone.now() - timedelta(days=7)
    flags = dict(
        ReviewFlag.objects.filter(status=ReviewFlag.Status.OPEN)
        .values_list("kind")
        .annotate(n=Count("pk"))
        .order_by()
    )
    scans = per_day(ProductVisit.objects.exclude(code=""))
    diary = per_day(DiaryEntry.objects.all())
    return {
        "users_total": User.objects.count(),
        "users_new_7d": User.objects.filter(date_joined__gte=week_ago).count(),
        "contributions_waiting": Contribution.objects.filter(
            status=Contribution.Status.SUBMITTED
        ).count(),
        "contributions_conflict": Contribution.objects.filter(
            status=Contribution.Status.CONFLICT
        ).count(),
        "flags_open": sum(flags.values()),
        "flags_by_kind": [(kind.label, flags.get(kind.value, 0)) for kind in ReviewFlag.Kind],
        "scans": scans,
        "scans_total": sum(r.count for r in scans),
        "diary": diary,
        "diary_total": sum(r.count for r in diary),
        "ai": ai_usage_this_month(),
        "site": SiteSettings.load(),
        "off": off_status(),
    }


# ---------- Review flags ----------


def _resolve(flag: ReviewFlag, user) -> None:
    flag.status = ReviewFlag.Status.RESOLVED
    flag.resolved_at = timezone.now()
    flag.resolved_by = user
    flag.save(update_fields=["status", "resolved_at", "resolved_by"])


def close_flag(flag_id: int, user, *, action: str = "flag_close", note: str = "") -> ReviewFlag:
    """Close an open flag without changing product data (also "keep local value")."""
    with transaction.atomic():
        flag = ReviewFlag.objects.select_for_update().get(pk=flag_id)
        if flag.status != ReviewFlag.Status.OPEN:
            raise StaffError("This flag is already closed.")
        _resolve(flag, user)
        log(
            user,
            action,
            f"flag {flag.pk}",
            kind=flag.kind,
            field=flag.field,
            product=str(flag.product_id or ""),
            note=note[:500],
        )
    return flag


def accept_off_value(flag_id: int, user, note: str = "") -> ReviewFlag:
    """Resolve a data conflict in favour of Open Food Facts.

    The field is unlocked and the latest OFF value stored on the current revision
    (``off_data``, written by the last import) is published as a new revision. No
    network call: later OFF refreshes keep the field in sync because it is unlocked.
    """
    with transaction.atomic():
        flag = ReviewFlag.objects.select_for_update().get(pk=flag_id)
        if flag.status != ReviewFlag.Status.OPEN:
            raise StaffError("This flag is already closed.")
        if flag.kind != ReviewFlag.Kind.DATA_CONFLICT or not flag.product_id:
            raise StaffError("Only data conflicts on a product can take the OFF value.")
        if flag.field not in DATA_FIELDS:
            raise StaffError(f"Unknown field {flag.field!r}.")
        product = Product.objects.select_for_update().get(pk=flag.product_id)
        current = product.current_revision
        if current is None or current.off_data is None:
            raise StaffError("No Open Food Facts data is stored for this product.")
        name = flag.field
        old_value = current.data.get(name)
        new_value = copy.deepcopy(current.off_data.get(name))

        product.locked_fields = [f for f in product.locked_fields if f != name]
        product.save(update_fields=["locked_fields", "updated_at"])
        data = copy.deepcopy(current.data)
        sources = copy.deepcopy(current.field_sources)
        data[name] = new_value
        sources[name] = {
            "source": "off",
            "off_last_modified": (
                current.off_last_modified.isoformat() if current.off_last_modified else None
            ),
            "accepted_from_flag": flag.pk,
        }
        revision = publish_revision(product, data, source="off_import", field_sources=sources)
        _resolve(flag, user)
        # Other open conflicts on the same field are settled by the same decision.
        for other in ReviewFlag.objects.filter(
            product=product,
            kind=ReviewFlag.Kind.DATA_CONFLICT,
            field=name,
            status=ReviewFlag.Status.OPEN,
        ).exclude(pk=flag.pk):
            _resolve(other, user)
        log(
            user,
            "flag_accept_off",
            f"flag {flag.pk}",
            product=str(product.pk),
            field=name,
            old_value=old_value,
            new_value=new_value,
            revision=revision.number if revision else None,
            note=note[:500],
        )
    return flag


# ---------- Products ----------


def queue_refresh(product: Product, user) -> bool:
    from catalog.services.lookup import schedule_refresh

    if not product.off_code:
        raise StaffError("This product has no Open Food Facts code.")
    queued = schedule_refresh(product)
    log(user, "product_refresh", str(product.pk), off_code=product.off_code, queued=queued)
    return queued


# ---------- Users ----------


def _guard_target(target, actor) -> None:
    if target.is_superuser:
        raise StaffError("Superusers cannot be changed from the staff panel.")
    if target.pk == actor.pk:
        raise StaffError("You cannot change your own account here.")


def set_active(target, actor, active: bool) -> None:
    _guard_target(target, actor)
    if target.is_active == active:
        return
    target.is_active = active
    target.save(update_fields=["is_active"])
    log(actor, "user_activate" if active else "user_deactivate", target.email, user_id=target.pk)


def resend_confirmation(request, target) -> str:
    from allauth.account.models import EmailAddress

    address = EmailAddress.objects.filter(user=target, verified=False).order_by("-primary").first()
    if address is None:
        if EmailAddress.objects.filter(user=target, verified=True).exists():
            raise StaffError("This email address is already confirmed.")
        if not target.email:
            raise StaffError("This account has no email address.")
        address = EmailAddress.objects.create(
            user=target, email=target.email, primary=True, verified=False
        )
    address.send_confirmation(request)
    log(request.user, "user_resend_confirmation", address.email, user_id=target.pk)
    return address.email


def delete_user(target, actor, typed: str) -> None:
    _guard_target(target, actor)
    if typed.strip().lower() != (target.email or target.get_username()).lower():
        raise StaffError("Type the account's email address exactly to confirm.")
    email, pk = target.email, target.pk
    with transaction.atomic():
        target.delete()
        log(actor, "user_delete", email, user_id=pk)
