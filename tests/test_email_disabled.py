"""A deployment without email needs no provider and never prepares a digest."""
import subprocess
import sys
from io import StringIO

import pytest
from django.core.management import call_command

from tests.test_production_settings import production_env


@pytest.mark.parametrize("explicit", [False, True])
def test_production_without_email_needs_no_resend_key(explicit):
    environment = production_env()
    environment.pop("REVIEW_EMAIL_DELIVERY")
    if explicit:
        environment["REVIEW_EMAIL_DELIVERY"] = "disabled"
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); from django.conf import settings; assert settings.REVIEW_EMAIL_DELIVERY == 'disabled'"],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dry_run", [False, True])
def test_disabled_digest_command_does_not_access_database_or_provider(settings, monkeypatch, dry_run):
    from study.management.commands import send_review_digest

    settings.REVIEW_EMAIL_DELIVERY = "disabled"

    def unexpected_provider():
        pytest.fail("Disabled email must not construct a provider")

    monkeypatch.setattr(send_review_digest, "get_delivery", unexpected_provider)
    output = StringIO()
    # Database access is forbidden in this test, including creating review slots.
    call_command("send_review_digest", dry_run=dry_run, stdout=output)
    assert output.getvalue().strip() == "Review email is disabled."
