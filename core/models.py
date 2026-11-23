"""Small coordination tables shared by web and worker processes (PostgreSQL only, no Redis)."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class RateCounter(models.Model):
    """Fixed-window counter, incremented atomically with INSERT ... ON CONFLICT."""

    key = models.CharField(max_length=200)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "window_start"], name="uniq_rate_window")
        ]

    def __str__(self) -> str:
        return f"{self.key}@{self.window_start}"


class Lease(models.Model):
    """Expiring exclusive lease. Acquired with one autocommit statement, never held in a
    transaction while doing network I/O."""

    key = models.CharField(max_length=200, unique=True)
    owner = models.CharField(max_length=64)
    expires_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.key


class LearnTopic(models.Model):
    """A Learn topic (the grid on /learn/). Managed in the admin."""

    key = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=80)
    icon = models.CharField(
        max_length=40, default="book-open", help_text="Icon name from the app's icon set."
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self) -> str:
        return self.title


class LearnArticle(models.Model):
    """A Learn article, written in the admin. The body uses a small, safe text format:
    blank line = new paragraph, "## " heading, "- " bullet, "1. " numbered item,
    **bold**, *italic*, [link text](/path or https://…)."""

    slug = models.SlugField(max_length=80, unique=True)
    topic = models.ForeignKey(LearnTopic, on_delete=models.PROTECT, related_name="articles")
    title = models.CharField(max_length=120)
    summary = models.CharField(
        max_length=400, help_text="A few lines under the title and in lists (max 400)."
    )
    body = models.TextField()
    pull_quote = models.CharField(
        max_length=220,
        blank=True,
        help_text="One striking sentence, shown large next to the text (magazine style).",
    )
    minutes = models.PositiveSmallIntegerField(default=3, help_text="Reading time in minutes.")
    image = models.ImageField(upload_to="learn/", blank=True, help_text="Landscape photo, 4:3.")
    image_static = models.CharField(
        max_length=200, blank=True, help_text="Bundled photo path (used when no image is uploaded)."
    )
    image_alt = models.CharField(max_length=160, blank=True)
    products = models.ManyToManyField(
        "catalog.Product",
        blank=True,
        related_name="learn_articles",
        help_text="Products shown at the end of the article.",
    )
    editors_pick = models.BooleanField(
        default=False,
        verbose_name="Editor’s pick",
        help_text="Shown in “Editor’s picks” on the Magazine cover (the newest 3).",
    )
    published = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    published_at = models.DateTimeField(
        default=timezone.now, help_text="Publication date; the newest come first under Latest."
    )
    # SEO
    seo_title = models.CharField(
        max_length=70, blank=True, help_text="Browser tab and search result title (max 70)."
    )
    meta_description = models.CharField(
        max_length=160,
        blank=True,
        help_text="Search result snippet (max 160). Defaults to the summary.",
    )
    canonical_url = models.URLField(
        blank=True, help_text="Only if the article lives elsewhere first."
    )
    noindex = models.BooleanField(default=False, help_text="Ask search engines not to index it.")
    # When "New on Learn" went to everyone's notifications (once per article).
    notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self) -> str:
        return self.title

    @property
    def topic_title(self) -> str:
        return self.topic.title

    @property
    def icon(self) -> str:
        return self.topic.icon

    @property
    def page_title(self) -> str:
        return self.seo_title or self.title

    @property
    def description(self) -> str:
        return self.meta_description or self.summary

    def image_url(self) -> str:
        from django.templatetags.static import static
        from django.urls import reverse

        if self.image:
            return (
                f"{reverse('learn-image', args=[self.slug])}?v={int(self.updated_at.timestamp())}"
            )
        return static(self.image_static) if self.image_static else ""


class MagazineCover(models.Model):
    """The Magazine cover: one issue only (no archive). Its month (on the phone cover card), its
    photo, and where the photo sits on the desktop cover and in the phone cover card. Managed
    in Staff → Magazine → Cover."""

    month = models.DateField(help_text="The month and year shown on the cover.")
    image = models.ImageField(
        upload_to="learn/covers/",
        blank=True,
        help_text="Any photo with plain space beside the person. Empty: the built-in photo.",
    )
    # Desktop: the photo lies under the titles and cards, over the whole 3:2 cover; in % of the
    # cover's height: its height, its left edge from the middle of the screen, its top edge.
    # The defaults fit a 3:2 photo to the whole cover.
    photo_size = models.PositiveSmallIntegerField(
        default=100, validators=[MinValueValidator(40), MaxValueValidator(300)]
    )
    photo_x = models.SmallIntegerField(
        default=-75, validators=[MinValueValidator(-300), MaxValueValidator(200)]
    )
    photo_y = models.SmallIntegerField(
        default=0, validators=[MinValueValidator(-100), MaxValueValidator(100)]
    )
    # Phones: the photo in the cover card (topics on its left). Its height in % of the card's
    # height (100: the whole 3:2 photo fills the 3:2 card), its left and top edges in % of the
    # card's width and height (minus: further left / higher).
    mobile_zoom = models.PositiveSmallIntegerField(
        default=100, validators=[MinValueValidator(40), MaxValueValidator(300)]
    )
    mobile_x = models.SmallIntegerField(
        default=0, validators=[MinValueValidator(-200), MaxValueValidator(100)]
    )
    mobile_y = models.SmallIntegerField(
        default=0, validators=[MinValueValidator(-200), MaxValueValidator(100)]
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.month.strftime("%B %Y")

    def save(self, *args, **kwargs):
        self.pk = 1  # one issue only
        self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "MagazineCover":
        cover = cls.objects.filter(pk=1).first()
        if cover is None:
            cover = cls(month=timezone.localdate())
            cover.save()
        return cover

    def image_url(self) -> str:
        from django.urls import reverse

        if not self.image:
            return ""
        version = int(self.updated_at.timestamp())
        return f"{reverse('learn-cover-image')}?v={version}"

    def style_url(self) -> str:
        from django.urls import reverse

        return f"{reverse('learn-cover-style')}?v={int(self.updated_at.timestamp())}"

    def style_css(self) -> str:
        """Where the photo sits on the desktop cover and in the phone cover card, as CSS
        variables (a stylesheet: no inline styles, CSP)."""
        return (
            f".mag-cover{{--photo-h:{int(self.photo_size)};--photo-x:{int(self.photo_x)};"
            f"--photo-y:{int(self.photo_y)};--m-zoom:{int(self.mobile_zoom)};"
            f"--m-x:{int(self.mobile_x)};--m-y:{int(self.mobile_y)}}}"
        )
