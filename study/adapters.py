from django.db import IntegrityError, transaction

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount

from study.models import PublisherIdentity, ReaderProfile
from study.services.identity import OWNER_EMAIL


class GoogleAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)
        if sociallogin.user and sociallogin.user.pk:
            self._provision_verified_google_login(sociallogin)

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        self._provision_verified_google_login(sociallogin)
        return user

    def _provision_verified_google_login(self, sociallogin):
        if not self._is_verified_google_login(sociallogin):
            return

        user = sociallogin.user
        assert user is not None and user.pk is not None
        subject = sociallogin.account.uid
        if not SocialAccount.objects.filter(
            user=user, provider="google", uid=subject
        ).exists():
            return
        is_owner = any(
            address.verified and address.email.casefold() == OWNER_EMAIL
            for address in sociallogin.email_addresses
        )
        with transaction.atomic():
            ReaderProfile.objects.get_or_create(
                user=user,
                defaults={"timezone": "Asia/Singapore" if is_owner else "UTC"},
            )
            if not is_owner:
                return
            if not EmailAddress.objects.filter(
                user=user, email__iexact=OWNER_EMAIL, verified=True
            ).exists():
                return
            try:
                PublisherIdentity.objects.get_or_create(
                    pk=1,
                    defaults={"user": user, "google_subject": subject},
                )
            except IntegrityError:
                # A concurrent verified login bound the singleton first. Never
                # replace that persisted subject or owner automatically.
                pass

    @staticmethod
    def _is_verified_google_login(sociallogin):
        return (
            sociallogin.account.provider == "google"
            and bool(sociallogin.account.uid)
            and any(address.verified for address in sociallogin.email_addresses)
        )
