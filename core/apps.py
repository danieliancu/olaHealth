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
