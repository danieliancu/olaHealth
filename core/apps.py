from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from core.context_processors import reset_learn_topics
        from core.models import LearnArticle, LearnTopic

        # The footer's topic list is cached per process: refresh it when topics change.
        post_save.connect(reset_learn_topics, sender=LearnTopic, dispatch_uid="footer-topics-save")
        post_delete.connect(reset_learn_topics, sender=LearnTopic, dispatch_uid="footer-topics-del")
        post_save.connect(reset_learn_topics, sender=LearnArticle, dispatch_uid="latest-save")
        post_delete.connect(reset_learn_topics, sender=LearnArticle, dispatch_uid="latest-del")
        # Ask Foodo's Magazine passages follow every edit, unpublish and delete at once.
        post_save.connect(_article_saved, sender=LearnArticle, dispatch_uid="passages-save")
        post_delete.connect(_article_deleted, sender=LearnArticle, dispatch_uid="passages-del")


def _article_saved(sender, instance, **kwargs) -> None:
    from django.db import transaction

    from core import passages

    if passages.index_article(instance) and instance.published:
        from django_q.tasks import async_task

        pk = instance.pk
        transaction.on_commit(lambda: async_task("core.passages.embed_article", pk))
    passages.bump_generation()


def _article_deleted(sender, instance, **kwargs) -> None:
    from core import passages

    passages.bump_generation()
