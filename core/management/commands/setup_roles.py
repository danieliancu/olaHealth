"""Create the two moderation groups with separate permissions."""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

VIEW = [
    "view_contribution",
    "view_contributionimage",
    "view_ocrrun",
    "view_moderationaudit",
    "view_product",
    "view_productrevision",
    "view_productimage",
    "view_reviewflag",
]
MODERATE = [*VIEW, "moderate_contribution"]


class Command(BaseCommand):
    help = "Create 'Reviewers' (read only) and 'Moderators' (approve/reject) groups."

    def handle(self, *args, **options) -> None:
        for name, codenames in (("Reviewers", VIEW), ("Moderators", MODERATE)):
            group, _ = Group.objects.get_or_create(name=name)
            perms = Permission.objects.filter(codename__in=codenames)
            group.permissions.set(perms)
            self.stdout.write(f"{name}: {perms.count()} permissions")
