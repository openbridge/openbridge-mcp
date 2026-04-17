"""Tests for sensitive-data sanitization."""

import pytest

from src.utils import security


class TestSanitizeString:
    def test_redacts_jwt_in_context(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcdef-signature_part"
        message = f"token received: {jwt} for user"

        sanitized = security.sanitize_string(message)

        assert jwt not in sanitized
        assert sanitized.startswith("token received: <jwt_token:REDACTED>")
        assert sanitized.endswith(" for user")

    def test_redacts_bearer_token_but_keeps_surrounding_text(self):
        message = "Authorization: Bearer abc123xyz — request failed with 401"

        sanitized = security.sanitize_string(message)

        assert "abc123xyz" not in sanitized
        assert "<bearer_token:REDACTED>" in sanitized
        assert "request failed with 401" in sanitized

    def test_api_key_requires_label_context(self):
        """A bare high-entropy alphanumeric string is not an API key."""
        account_id = "a1b2c3d4e5f6g7h8i9j0a1b2c3d4e5f6g7h8"
        message = f"processing account {account_id} for subscription 42"

        sanitized = security.sanitize_string(message)

        assert sanitized == message, (
            "Contextless alphanumerics must be preserved to avoid destroying logs"
        )

    @pytest.mark.parametrize(
        "labelled",
        [
            "api_key=sk_live_abcdef12345",
            "apikey: my-secret-value-123",
            "x-api-key = 'abcdef1234567890'",
            "client_secret: mysupersecret123",
            "refresh_token=xxxxxxxxxxxxxx",
        ],
    )
    def test_labelled_secrets_are_redacted(self, labelled):
        sanitized = security.sanitize_string(f"log line with {labelled} trailing")

        assert "<api_key:REDACTED>" in sanitized
        assert "trailing" in sanitized

    def test_uuid_is_preserved(self):
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        message = f"subscription_id={uuid} stage_id=1004"

        sanitized = security.sanitize_string(message)

        assert uuid in sanitized

    def test_table_name_is_preserved(self):
        message = "SELECT * FROM amzn_ads_sb_campaigns LIMIT 10"

        sanitized = security.sanitize_string(message)

        assert sanitized == message

    def test_partial_mode_uses_length_hint_per_match(self):
        jwt = "eyJabc.defghi.jklmno_signature"
        message = f"got {jwt} back"

        sanitized = security.sanitize_string(message, partial=True)

        assert jwt not in sanitized
        assert f"<jwt_token:length={len(jwt)}>" in sanitized
        assert "got " in sanitized and " back" in sanitized

    def test_empty_string_returns_unchanged(self):
        assert security.sanitize_string("") == ""
        assert security.sanitize_string(None) is None  # type: ignore[arg-type]
