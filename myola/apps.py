from django.apps import AppConfig


class MyOlaConfig(AppConfig):
    """My Ola MVP: preferences, restriction states and the personal space.

    No models: everything personal stays in the browser (see docs/mvp-api-contracts.md).
    """

    name = "myola"
    verbose_name = "My Ola (MVP)"
