from __future__ import annotations

import pytest

from project_glacier_mcp.errors import ConfigurationError, ValidationError
from project_glacier_mcp.models import Category, validate_category
from project_glacier_mcp.settings import EXPECTED_ORIGIN, Settings


def test_settings_accept_exact_origin_and_environment_credential(tmp_path):
    settings = Settings.from_environment(
        {
            "GLACIER_BASE_URL": EXPECTED_ORIGIN,
            "GLACIER_USER_ID": "123456789",
            "GLACIER_STATE_DIR": str(tmp_path),
        },
        keychain_reader=lambda _service: None,
    )

    assert settings.base_url == EXPECTED_ORIGIN
    assert settings.user_id == "123456789"
    assert settings.allow_submit is False


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://144.25.123.139:8653",
        "http://144.25.123.139:8654",
        "http://example.com:8653",
        "http://user@144.25.123.139:8653",
        "http://144.25.123.139:8653/evaluation",
        "http://144.25.123.139:8653?next=example.com",
    ],
)
def test_settings_reject_any_non_allowlisted_origin(unsafe_url, tmp_path):
    with pytest.raises(ConfigurationError, match="exact authorized challenge origin"):
        Settings.from_environment(
            {
                "GLACIER_BASE_URL": unsafe_url,
                "GLACIER_USER_ID": "123456789",
                "GLACIER_STATE_DIR": str(tmp_path),
            },
            keychain_reader=lambda _service: None,
        )


def test_settings_uses_keychain_without_exposing_value(tmp_path):
    settings = Settings.from_environment(
        {"GLACIER_STATE_DIR": str(tmp_path)},
        keychain_reader=lambda service: "123456789" if service else None,
    )

    assert settings.user_id == "123456789"


def test_settings_fail_closed_without_credential(tmp_path):
    with pytest.raises(ConfigurationError) as caught:
        Settings.from_environment(
            {"GLACIER_STATE_DIR": str(tmp_path)},
            keychain_reader=lambda _service: None,
        )

    assert caught.value.code == "AUTH_MISSING"
    assert "123456789" not in str(caught.value)


def test_settings_reject_relative_state_directory():
    with pytest.raises(ConfigurationError, match="must be an absolute path"):
        Settings.from_environment(
            {
                "GLACIER_USER_ID": "123456789",
                "GLACIER_STATE_DIR": ".glacier-state",
            },
            keychain_reader=lambda _service: None,
        )


def test_exact_challenge_category_values_are_enforced():
    category, subcategory = validate_category(Category.CONTENT_MODERATION.value, "")
    assert category is Category.CONTENT_MODERATION
    assert subcategory == ""

    with pytest.raises(ValidationError):
        validate_category("Prompt Injection Detection", "")

    with pytest.raises(ValidationError):
        validate_category(Category.CONTENT_MODERATION.value, "Other")

    category, subcategory = validate_category(
        Category.BUSINESS_RULES.value,
        "Internal System Exposure",
    )
    assert category is Category.BUSINESS_RULES
    assert subcategory == "Internal System Exposure"
