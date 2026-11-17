import httpx
import pytest
import respx

from off.client import OFFClient, OFFError

BASE = "https://off.test"
URL = f"{BASE}/api/v2/product/5941234567899.json"


def client(**kw):
    sleeps = []
    c = OFFClient(base_url=BASE, user_agent="olaHealth-tests/1.0 (test)", sleep=sleeps.append, **kw)
    return c, sleeps


@respx.mock
def test_success_sends_user_agent_and_fields(off_json):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=off_json()))
    c, _ = client()
    product = c.get_product("5941234567899")
    assert product["product_name"] == "Greek style yogurt"
    request = route.calls.last.request
    assert request.headers["User-Agent"] == "olaHealth-tests/1.0 (test)"
    assert "nutriscore_grade" in request.url.params["fields"]


@respx.mock
@pytest.mark.parametrize("status", [200, 404])
def test_explicit_not_found(status):
    respx.get(URL).mock(
        return_value=httpx.Response(
            status, json={"status": 0, "status_verbose": "product not found"}
        )
    )
    with pytest.raises(OFFError) as exc:
        client()[0].get_product("5941234567899")
    assert exc.value.kind == "not_found"


@respx.mock
def test_404_without_json_is_invalid_response_not_not_found():
    respx.get(URL).mock(return_value=httpx.Response(404, text="<html>nope</html>"))
    with pytest.raises(OFFError) as exc:
        client()[0].get_product("5941234567899")
    assert exc.value.kind == "invalid_response"


@respx.mock
def test_timeout_is_retried_then_reported():
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    c, sleeps = client(max_retries=2)
    with pytest.raises(OFFError) as exc:
        c.get_product("5941234567899")
    assert exc.value.kind == "timeout"
    assert route.call_count == 3
    assert len(sleeps) == 2


@respx.mock
def test_transient_5xx_then_success(off_json):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=off_json())]
    )
    c, sleeps = client()
    assert c.get_product("5941234567899")["code"] == "5941234567899"
    assert route.call_count == 2
    assert len(sleeps) == 1


@respx.mock
def test_upstream_unavailable_after_retries():
    respx.get(URL).mock(return_value=httpx.Response(502))
    with pytest.raises(OFFError) as exc:
        client(max_retries=1)[0].get_product("5941234567899")
    assert exc.value.kind == "upstream_unavailable"


@respx.mock
def test_rate_limited_honours_short_retry_after(off_json):
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=off_json()),
        ]
    )
    c, sleeps = client(retry_after_cap=5)
    c.get_product("5941234567899")
    assert sleeps == [2.0]


@respx.mock
def test_rate_limited_with_long_retry_after_is_not_waited():
    respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    c, sleeps = client(retry_after_cap=5)
    with pytest.raises(OFFError) as exc:
        c.get_product("5941234567899")
    assert exc.value.kind == "rate_limited"
    assert exc.value.retry_after == 120
    assert sleeps == []


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"status": 1}),
        httpx.Response(302, headers={"Location": "https://elsewhere.example/x"}),
    ],
)
def test_invalid_responses(response):
    respx.get(URL).mock(return_value=response)
    with pytest.raises(OFFError) as exc:
        client()[0].get_product("5941234567899")
    assert exc.value.kind == "invalid_response"


@respx.mock
def test_code_mismatch_is_invalid(off_json):
    payload = off_json()
    payload["product"]["code"] = "1111111111116"
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(OFFError) as exc:
        client()[0].get_product("5941234567899")
    assert exc.value.kind == "invalid_response"


def test_rate_gate_denial_blocks_the_request():
    def gate():
        raise OFFError("rate_limited", "local", retry_after=40)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(URL)
        with pytest.raises(OFFError) as exc:
            client(rate_gate=gate)[0].get_product("5941234567899")
        assert exc.value.kind == "rate_limited"
        assert route.call_count == 0


def test_non_numeric_code_is_invalid():
    with pytest.raises(OFFError) as exc:
        client()[0].get_product("12ab")
    assert exc.value.kind == "invalid_code"
