"""Static files storage for production.

WhiteNoise's compressed manifest storage, plus Django's rewriting of JavaScript module
``import`` statements to the hashed file names. Without it a module imported by its plain
name (``./app.js``) would be loaded a second time next to the hashed copy referenced by
the template, running its event handlers twice.
"""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class StaticStorage(CompressedManifestStaticFilesStorage):
    support_js_module_import_aggregation = True
