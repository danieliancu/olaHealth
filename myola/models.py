"""Personal data of a FoodoTrack account: profile, food diary, water, activity, weight, product
lists, scans, notes, memories and chats.

Everything here belongs to one user and is deleted with the account (CASCADE). None of it ever
reaches the catalogue, the moderation queue or the public export.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from myola import preferences as P

User = settings.AUTH_USER_MODEL


def _choices(mapping: dict) -> list[tuple[str, str]]:
    return [(k, v[0] if isinstance(v, tuple) else v) for k, v in mapping.items()]


GOAL_CHOICES = _choices(P.GOALS)
GENDER_CHOICES = _choices(P.GENDERS)
RESTRICTION_CHOICES = _choices(P.RESTRICTIONS)
NUT_CHOICES = _choices(P.NUT_TYPES)
SEAFOOD_CHOICES = _choices(P.SEAFOOD_TYPES)
CONDITION_CHOICES = _choices(P.CONDITIONS)
ACTIVITY_CHOICES = _choices(P.ACTIVITIES)


class Meal(models.TextChoices):
    BREAKFAST = "breakfast", _("Breakfast")
    LUNCH = "lunch", _("Lunch")
    DINNER = "dinner", _("Dinner")
    SNACKS = "snacks", _("Snacks")


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    # Onboarding answers (all optional).
    goal = models.CharField(max_length=20, choices=GOAL_CHOICES, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True)
    age = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(13), MaxValueValidator(120)]
    )
    height = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(100)), MaxValueValidator(Decimal(250))],
    )
    weight = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(25)), MaxValueValidator(Decimal(400))],
    )
    restrictions = ArrayField(
        models.CharField(max_length=20, choices=RESTRICTION_CHOICES), default=list, blank=True
    )
    nut_types = ArrayField(
        models.CharField(max_length=20, choices=NUT_CHOICES), default=list, blank=True
    )
    seafood_types = ArrayField(
        models.CharField(max_length=20, choices=SEAFOOD_CHOICES), default=list, blank=True
    )
    religious_note = models.CharField(max_length=300, blank=True)
    other_note = models.CharField(max_length=300, blank=True)
    # Health data (special category): saved only with explicit consent.
    conditions = ArrayField(
        models.CharField(max_length=20, choices=CONDITION_CHOICES), default=list, blank=True
    )
    conditions_note = models.CharField(max_length=300, blank=True)
    health_consent_at = models.DateTimeField(null=True, blank=True)
    onboarded = models.BooleanField(default=False)
    # Settings
    use_notes = models.BooleanField(default=False)
    show_energy = models.BooleanField(default=True)
    show_targets = models.BooleanField(default=True)
    track_weight = models.BooleanField(default=False)
    target_kcal = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10000)]
    )
    target_protein = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(1000)]
    )
    target_carbs = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(2000)]
    )
    target_fat = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(1000)]
    )
    timezone = models.CharField(max_length=64, default="Europe/London")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile of {self.user_id}"

    @property
    def eating_disorder(self) -> bool:
        """An eating-disorder history hides energy numbers and weight tracking."""
        return "eating_disorder" in self.conditions

    @property
    def targets(self) -> dict[str, int | None]:
        return {
            "energy-kcal": self.target_kcal,
            "proteins": self.target_protein,
            "carbohydrates": self.target_carbs,
            "fat": self.target_fat,
        }


class DiaryEntry(models.Model):
    """One food in the diary. ``per100`` values are a snapshot taken when the entry was saved:
    later catalogue updates never change past entries."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="diary_entries")
    date = models.DateField()
    meal = models.CharField(max_length=10, choices=Meal.choices)
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=200, blank=True)
    image = models.CharField(max_length=500, blank=True)
    amount = models.DecimalField(
        max_digits=7,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal(10000))],
    )
    unit = models.CharField(max_length=2, choices=[("g", "g"), ("ml", "ml")])
    basis_known = models.BooleanField(default=True)
    portion = models.CharField(max_length=80, blank=True)
    kcal_100 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    protein_100 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    carbs_100 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    fat_100 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]
        indexes = [models.Index(fields=["user", "date"])]
        verbose_name_plural = "diary entries"

    def __str__(self) -> str:
        return f"{self.date} {self.meal}: {self.name}"


class WaterEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="water_entries")
    date = models.DateField()
    ml = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5000)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]
        indexes = [models.Index(fields=["user", "date"])]
        verbose_name_plural = "water entries"


class ActivityEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="activity_entries")
    date = models.DateField()
    kind = models.CharField(max_length=20, choices=ACTIVITY_CHOICES)
    minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]
        indexes = [models.Index(fields=["user", "date"])]
        verbose_name_plural = "activity entries"


class WeightEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="weight_entries")
    date = models.DateField()
    kg = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        validators=[MinValueValidator(Decimal(25)), MaxValueValidator(Decimal(400))],
    )

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="one_weight_per_day")
        ]
        verbose_name_plural = "weight entries"


class ProductMark(models.Model):
    """A product the user marked: favourite, buy often, avoid, or hidden from lists.
    Buy often and avoid exclude each other (enforced in ``myola.services.lists``)."""

    class Kind(models.TextChoices):
        FAVORITE = "favorite", _("Favourite")
        BUY_OFTEN = "buy_often", _("Buy often")
        AVOID = "avoid", _("Avoid")
        HIDDEN = "hidden", _("Hidden")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="product_marks")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "product", "kind"], name="one_mark_per_kind")
        ]
        indexes = [models.Index(fields=["user", "kind", "-created_at"])]


class ProductVisit(models.Model):
    """Recently viewed products; a visit with a barcode is a scan."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="product_visits")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    @property
    def is_scan(self) -> bool:
        return bool(self.code)


class Note(models.Model):
    class Kind(models.TextChoices):
        GENERAL = "general", _("General")
        TASTE = "taste", _("Taste")
        PREFER = "prefer", _("I prefer")
        AVOID = "avoid", _("I avoid")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GENERAL)
    text = models.TextField(max_length=2000)
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class Memory(models.Model):
    """Something Ask Foodo may use, only while active and confirmed by the user."""

    class Source(models.TextChoices):
        MANUAL = "manual", _("Added by you")
        SUGGESTION = "suggestion", _("Suggested")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memories")
    text = models.CharField(max_length=300)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    suggestion_key = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "memories"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", _("You")
        OLA = "ola", _("Foodo")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_messages")
    # "personal" or "product:<uuid>"
    context = models.CharField(max_length=60, default="personal")
    role = models.CharField(max_length=5, choices=Role.choices)
    text = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["user", "context", "created_at"])]


# Per-user limits (as in the former browser store).
LIMITS = {
    "diary": 3000,
    "water": 3000,
    "activity": 2000,
    "weights": 1000,
    "marks": 300,
    "visits": 100,
    "notes": 300,
    "memories": 100,
    "chat_messages": 60,
}
