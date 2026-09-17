import pytest

from telegram_onboarding import TelegramOnboarding, TelegramOnboardingError


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+33 6 12 34 56 78", "+33612345678"),
        ("33612345678", "+33612345678"),
        ("+1 (415) 555-2671", "+14155552671"),
    ],
)
def test_normalize_phone(raw, expected):
    assert TelegramOnboarding.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "+12", "+33 6 12 34 xx 78"])
def test_invalid_phone_is_rejected(raw):
    with pytest.raises(TelegramOnboardingError):
        TelegramOnboarding.normalize_phone(raw)
