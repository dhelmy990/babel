from django.core.exceptions import PermissionDenied

from study.models import PublisherIdentity, ReaderProfile


OWNER_EMAIL = "dhelmy990@gmail.com"


def profile_for(user):
    profile, _ = ReaderProfile.objects.get_or_create(user=user)
    return profile


def is_publisher(user) -> bool:
    if not user.is_authenticated:
        return False
    try:
        binding = PublisherIdentity.objects.get(pk=1, user=user)
    except PublisherIdentity.DoesNotExist:
        return False

    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    return (
        SocialAccount.objects.filter(
            user=user, provider="google", uid=binding.google_subject
        ).exists()
        and EmailAddress.objects.filter(
            user=user, email__iexact=OWNER_EMAIL, verified=True
        ).exists()
    )


def require_publisher(user) -> None:
    if not is_publisher(user):
        raise PermissionDenied("Publishing requires the verified owner identity.")
