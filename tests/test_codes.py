import pytest

from off.codes import InvalidCode, expand_upce, gs1_check_digit, off_normalize, parse_code


@pytest.mark.parametrize(
    ("code", "fmt"),
    [
        ("5941234567899", "ean13"),
        ("96385074", "ean8"),
        ("036000291452", "upca"),
        ("00012345678905", "itf14"),
    ],
)
def test_valid_gtins_keep_original_and_pad_to_gtin14(code, fmt):
    parsed = parse_code(code, fmt)
    assert parsed.raw == code
    assert parsed.detected_format == fmt
    assert parsed.check_digit_valid is True
    assert parsed.gtin14 == code.zfill(14)
    assert parsed.kind == "gtin"


def test_check_digit_algorithm():
    assert gs1_check_digit("594123456789") == 9
    assert gs1_check_digit("03600029145") == 2


def test_bad_check_digit_is_rejected_unless_explicitly_allowed():
    with pytest.raises(InvalidCode) as exc:
        parse_code("5941234567890")
    assert exc.value.reason == "bad_check_digit"
    parsed = parse_code("5941234567890", allow_bad_check=True)
    assert parsed.kind == "unverified"
    assert parsed.gtin14 is None
    assert parsed.check_digit_valid is False


def test_check_digit_not_applied_to_lengths_without_a_rule():
    parsed = parse_code("1234567890")  # 10 digits: no GS1 rule applies
    assert parsed.check_digit_valid is None
    assert parsed.kind == "unverified"


@pytest.mark.parametrize("raw", ["", "abc", "12a45678", "123", "123456789012345"])
def test_invalid_codes(raw):
    with pytest.raises(InvalidCode):
        parse_code(raw)


def test_off_normalisation_follows_documented_rules():
    # Documented example: 12-digit 034000470693 is stored as 0034000470693.
    assert off_normalize("034000470693") == "0034000470693"
    assert off_normalize("0034000470693") == "0034000470693"
    assert off_normalize("00000000012345") == "00012345"  # <=7 digits -> 8
    assert off_normalize("96385074") == "96385074"
    assert off_normalize("5941234567899") == "5941234567899"


def test_leading_zeros_are_not_dropped_from_identity():
    upca = parse_code("036000291452", "upca")
    ean = parse_code("0036000291452", "ean13")
    assert upca.gtin14 == ean.gtin14 == "00036000291452"
    assert upca.raw != ean.raw  # originals preserved


def test_upce_expands_to_same_identity_as_upca():
    assert expand_upce("04252614") == "042100005264"
    upce = parse_code("04252614", "upce")
    upca = parse_code("042100005264", "upca")
    assert upce.gtin14 == upca.gtin14
    assert upce.detected_format == "upce"


@pytest.mark.parametrize("code", ["2000000000015", "0200000000006", "0400000000002"])
def test_restricted_circulation_codes_are_flagged(code):
    code = code[:-1] + str(gs1_check_digit(code[:-1]))
    assert parse_code(code).kind == "restricted"


def test_separators_are_ignored_but_raw_kept():
    parsed = parse_code(" 594-1234 567899 ")
    assert parsed.digits == "5941234567899"
    assert parsed.raw == " 594-1234 567899 "
