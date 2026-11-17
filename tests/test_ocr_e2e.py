"""End-to-end OCR on the synthetic label fixtures with the real Tesseract binary.

Not part of the default run (slow): ``pytest -m ocr``.
"""

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from contributions.models import OCRRun
from contributions.services import drafts, ocr

pytestmark = [pytest.mark.ocr, pytest.mark.django_db]
FIXTURES = Path(__file__).parent / "fixtures" / "ocr"

EXPECTED = {
    "label_en.jpg": {
        "energy-kj": "1966",
        "energy-kcal": "470",
        "fat": "21",
        "saturated-fat": "9.8",
        "carbohydrates": "61",
        "sugars": "28",
        "fiber": "2.5",
        "proteins": "6.2",
        "salt": "0.48",
    },
    "label_ro.jpg": {
        "energy-kj": "1580",
        "energy-kcal": "377",
        "fat": "12",
        "saturated-fat": "4.5",
        "carbohydrates": "58.2",
        "sugars": "22",
        "fiber": "3.1",
        "proteins": "7",
        "salt": "0.9",
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_ocr_and_parser_on_label(name):
    c = drafts.create_draft(session_key="s", problem_type="new_product", code="5900000000008")
    content = (FIXTURES / name).read_bytes()
    img = drafts.add_image(c, SimpleUploadedFile(name, content, "image/jpeg"), "nutrition")
    run = OCRRun.objects.get(image=img)
    assert ocr.run_ocr(run.pk) == "done"
    run.refresh_from_db()
    found = {p["nutrient"]: p["value"] for p in run.parsed["proposals"]}
    expected = EXPECTED[name]
    correct = {k for k, v in expected.items() if found.get(k) == v}
    wrong = {k: found[k] for k in found if k in expected and found[k] != expected[k]}
    print(f"{name}: {len(correct)}/{len(expected)} correct, {run.duration_ms} ms, wrong={wrong}")
    assert run.parsed["basis"] == "per_100g"
    assert len(correct) >= 7, (found, run.text)
    assert run.words and all("conf" in w for w in run.words)
