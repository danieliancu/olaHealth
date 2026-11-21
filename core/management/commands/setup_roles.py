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
EDIT_LEARN = [
    "view_learnarticle", "add_learnarticle", "change_learnarticle", "delete_learnarticle",
    "view_learntopic", "add_learntopic", "change_learntopic", "delete_learntopic",
]  # fmt: skip


class Command(BaseCommand):
    help = "Create 'Reviewers', 'Moderators' (approve/reject) and 'Editors' (Learn) groups."

    def handle(self, *args, **options) -> None:
        groups = (("Reviewers", VIEW), ("Moderators", MODERATE), ("Editors", EDIT_LEARN))
        for name, codenames in groups:
            group, _ = Group.objects.get_or_create(name=name)
            perms = Permission.objects.filter(codename__in=codenames)
            group.permissions.set(perms)
            self.stdout.write(f"{name}: {perms.count()} permissions")
