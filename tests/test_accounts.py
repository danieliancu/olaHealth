from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

PERSONAL_PAGES = ("start", "today", "progress", "my-ola", "delete-account")
PUBLIC_PAGES = ("home", "scan", "products", "compare", "sources")


@pytest.mark.django_db
@pytest.mark.parametrize("name", PERSONAL_PAGES)
def test_personal_pages_need_an_account(client, name):
    r = client.get(reverse(name))
    assert r.status_code == 302
    assert r["Location"].startswith(reverse("account_login") + "?next=")


@pytest.mark.django_db
@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_scan_and_catalogue_stay_public(client, name):
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.django_db
def test_signed_in_home_opens_today(member_client):
    r = member_client.get(reverse("home"))
    assert r.status_code == 302 and r["Location"] == reverse("today")


@pytest.mark.django_db
def test_signup_requires_email_confirmation_then_opens_onboarding(client):
    r = client.post(
        reverse("account_signup"),
        {
            "email": "new@example.com",
            "password1": "a-long-test-password-1",
            "password2": "a-long-test-password-1",
        },
    )
    assert r.status_code == 302
    user = get_user_model().objects.get(email="new@example.com")
    # Not signed in until the address is confirmed.
    assert client.get(reverse("today")).status_code == 302
    assert len(mail.outbox) == 1 and "new@example.com" in mail.outbox[0].to
    link = re.search(r"https?://[^/\s]+(/accounts/confirm-email/\S+)", mail.outbox[0].body)
    assert link, mail.outbox[0].body
    page = client.get(link.group(1))
    assert page.status_code == 200
    r = client.post(link.group(1))
    assert r.status_code == 302 and r["Location"] == reverse("start")
    assert client.get(reverse("today")).status_code == 200
    assert user.emailaddress_set.get().verified


@pytest.mark.django_db
def test_sign_in_with_email_and_password(client, member):
    r = client.post(
        reverse("account_login"),
        {"login": "member@example.com", "password": "a-long-test-password-1"},
    )
    assert r.status_code == 302 and r["Location"] == reverse("today")


@pytest.mark.django_db
def test_wrong_password_is_refused(client, member):
    r = client.post(reverse("account_login"), {"login": "member@example.com", "password": "nope"})
    assert r.status_code == 200
    assert client.get(reverse("today")).status_code == 302


@pytest.mark.django_db
def test_auth_pages_use_the_app_shell_without_inline_scripts(client):
    for name in ("account_login", "account_signup", "account_reset_password"):
        html = client.get(reverse(name)).content.decode()
        assert '<nav class="tabbar"' in html and 'class="card stack auth-form"' in html
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag, (name, tag)


@pytest.mark.django_db
def test_account_tab_shows_the_email_and_real_actions(member_client):
    html = member_client.get(reverse("my-ola") + "?tab=account").content.decode()
    assert "member@example.com" in html
    assert reverse("account_change_password") in html and reverse("delete-account") in html
    assert "coming soon" not in html.lower()


@pytest.mark.django_db
def test_delete_account_needs_the_password_and_the_word(member_client, member):
    url = reverse("delete-account")
    r = member_client.post(url, {"password": "wrong", "confirm": "DELETE"})
    assert r.status_code == 200 and get_user_model().objects.filter(pk=member.pk).exists()
    r = member_client.post(url, {"password": "a-long-test-password-1", "confirm": "nope"})
    assert r.status_code == 200 and get_user_model().objects.filter(pk=member.pk).exists()
    r = member_client.post(url, {"password": "a-long-test-password-1", "confirm": "delete"})
    assert r.status_code == 302 and r["Location"] == reverse("home")
    assert not get_user_model().objects.filter(pk=member.pk).exists()
    assert member_client.get(reverse("today")).status_code == 302
