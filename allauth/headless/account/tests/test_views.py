from unittest.mock import ANY

from django.urls import reverse

import pytest

from allauth.account.models import EmailAddress, get_emailconfirmation_model


def test_auth_password_input_error(client):
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "status": 400,
        "error": {
            "detail": {
                "__all__": ["Missing username."],
                "password": ["This field is required."],
            }
        },
    }


def test_auth_password_bad_password(client, user, settings):
    settings.ACCOUNT_AUTHENTICATION_METHOD = "email"
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={
            "email": user.email,
            "password": "wrong",
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "status": 400,
        "error": {
            "detail": {
                "password": [
                    "The email address and/or password you "
                    "specified are not correct."
                ]
            }
        },
    }


def test_auth_password_success(client, user, user_password, settings):
    settings.ACCOUNT_AUTHENTICATION_METHOD = "email"
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={
            "email": user.email,
            "password": user_password,
        },
        content_type="application/json",
    )
    assert resp.status_code == 200
    resp = client.get(
        reverse("headless_auth", args=["browser"]),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "status": 200,
        "data": {
            "user": {
                "id": user.pk,
                "display": str(user),
                "email": user.email,
                "username": user.username,
            },
            "methods": [
                {
                    "at": ANY,
                    "email": user.email,
                    "method": "password",
                }
            ],
        },
        "meta": {"is_authenticated": True},
    }


def test_auth_unverified_email(
    client,
    user_factory,
    password_factory,
    settings,
):
    settings.ACCOUNT_AUTHENTICATION_METHOD = "email"
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    password = password_factory()
    user = user_factory(email_verified=False, password=password)
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={
            "email": user.email,
            "password": password,
        },
        content_type="application/json",
    )
    assert resp.status_code == 401
    # FIXME
    # assert resp.json() == {}
    emailaddress = EmailAddress.objects.filter(user=user, verified=False).get()
    key = get_emailconfirmation_model().create(emailaddress).key
    resp = client.post(
        reverse("headless_verify_email", args=["browser"]),
        data={"key": key},
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_verify_email_bad_key(client, settings, password_factory, user_factory):
    settings.ACCOUNT_AUTHENTICATION_METHOD = "email"
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    password = password_factory()
    user = user_factory(email_verified=False, password=password)
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={
            "email": user.email,
            "password": password,
        },
        content_type="application/json",
    )
    assert resp.status_code == 401
    resp = client.post(
        reverse("headless_verify_email", args=["browser"]),
        data={"key": "bad"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "status": 400,
        "error": {"detail": {"key": ["Invalid or expired key."]}},
    }


@pytest.mark.parametrize("is_active,status_code", [(False, 401), (True, 200)])
def test_auth_password_user_inactive(
    client, user, user_password, settings, status_code, is_active
):
    user.is_active = is_active
    user.save(update_fields=["is_active"])
    resp = client.post(
        reverse("headless_login", args=["browser"]),
        data={
            "username": user.username,
            "password": user_password,
        },
        content_type="application/json",
    )
    assert resp.status_code == status_code


def test_password_reset_flow(client, user, mailoutbox, password_factory, settings):
    settings.ACCOUNT_EMAIL_NOTIFICATIONS = True

    resp = client.post(
        reverse("headless_request_password_reset", args=["browser"]),
        data={
            "email": user.email,
        },
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    # Extract URL for `password_reset_from_key` view
    url = body[body.find("/password/reset/") :].split()[0]
    key = url.split("/")[-2]
    password = password_factory()

    # Too simple password
    resp = client.post(
        reverse("headless_reset_password", args=["browser"]),
        data={
            "key": key,
            "password": "a",
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "status": 400,
        "error": {
            "detail": {
                "__all__": [
                    "This password is too short. It must contain at least 6 characters."
                ]
            }
        },
    }

    assert len(mailoutbox) == 1

    # Success
    resp = client.post(
        reverse("headless_reset_password", args=["browser"]),
        data={
            "key": key,
            "password": password,
        },
        content_type="application/json",
    )
    assert resp.status_code == 200

    user.refresh_from_db()
    assert user.check_password(password)
    assert len(mailoutbox) == 2  # The security notification


def test_password_reset_flow_wrong_key(client, password_factory):
    password = password_factory()
    resp = client.post(
        reverse("headless_reset_password", args=["browser"]),
        data={
            "key": "wrong",
            "password": password,
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "status": 400,
        "error": {"detail": {"key": ["The password reset token was invalid."]}},
    }


@pytest.mark.parametrize(
    "has_password,request_data,response_data,status_code",
    [
        # Wrong current password
        (
            True,
            {"current_password": "wrong", "new_password": "{password_factory}"},
            {
                "status": 400,
                "error": {
                    "detail": {
                        "current_password": ["Please type your current password."]
                    }
                },
            },
            400,
        ),
        # Happy flow, regular password change
        (
            True,
            {
                "current_password": "{user_password}",
                "new_password": "{password_factory}",
            },
            {
                "status": 200,
            },
            200,
        ),
        # New password does not match constraints
        (
            True,
            {
                "current_password": "{user_password}",
                "new_password": "a",
            },
            {
                "status": 400,
                "error": {
                    "detail": {
                        "new_password": [
                            "This password is too short. It must contain at least 6 characters."
                        ]
                    }
                },
            },
            400,
        ),
        # New password not empty
        (
            True,
            {
                "current_password": "{user_password}",
                "new_password": "",
            },
            {
                "status": 400,
                "error": {"detail": {"new_password": ["This field is required."]}},
            },
            400,
        ),
        # Current password not blank
        (
            True,
            {
                "current_password": "",
                "new_password": "{password_factory}",
            },
            {
                "status": 400,
                "error": {"detail": {"current_password": ["This field is required."]}},
            },
            400,
        ),
        # Current password missing
        (
            True,
            {
                "new_password": "{password_factory}",
            },
            {
                "status": 400,
                "error": {"detail": {"current_password": ["This field is required."]}},
            },
            400,
        ),
        # Current password not set, happy flow
        (
            False,
            {
                "current_password": "",
                "new_password": "{password_factory}",
            },
            {
                "status": 200,
            },
            200,
        ),
        # Current password not set, current_password absent
        (
            False,
            {
                "new_password": "{password_factory}",
            },
            {
                "status": 200,
            },
            200,
        ),
    ],
)
def test_change_password(
    auth_client,
    user,
    request_data,
    response_data,
    status_code,
    has_password,
    user_password,
    password_factory,
    settings,
    mailoutbox,
):
    settings.ACCOUNT_EMAIL_NOTIFICATIONS = True
    if not has_password:
        user.set_unusable_password()
        user.save(update_fields=["password"])
        auth_client.force_login(user)
    if request_data.get("current_password") == "{user_password}":
        request_data["current_password"] = user_password
    if request_data.get("new_password") == "{password_factory}":
        request_data["new_password"] = password_factory()
    resp = auth_client.post(
        reverse("headless_change_password", args=["browser"]),
        data=request_data,
        content_type="application/json",
    )
    assert resp.status_code == status_code
    assert resp.json() == response_data
    user.refresh_from_db()
    if resp.status_code == 200:
        assert user.check_password(request_data["new_password"])
        assert len(mailoutbox) == 1
    else:
        assert user.check_password(user_password)
        assert len(mailoutbox) == 0