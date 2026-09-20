"""Small delivery adapters. Provider errors expose categories, never response bodies."""
import hashlib
import json

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


ERROR_CODES = {
    "authentication_rejected", "invalid_payload", "idempotency_mismatch",
    "request_rejected", "concurrent_request", "rate_limited", "provider_unavailable",
    "transport_error", "invalid_response", "delivery_error",
}


class DeliveryError(Exception):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "delivery_error"
        super().__init__(self.code)


class PermanentDeliveryError(DeliveryError):
    pass


class RetryableDeliveryError(DeliveryError):
    pass


def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def valid_message_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class ResendDelivery:
    def __init__(self, api_key, *, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ImproperlyConfigured("RESEND_API_KEY is required for Resend delivery")
        self.api_key = api_key
        self.transport = transport

    def send(self, payload: dict, idempotency_key: str) -> str:
        try:
            with httpx.Client(timeout=10.0, transport=self.transport) as client:
                response = client.post(
                    "https://api.resend.com/emails", content=canonical_json(payload),
                    headers={"Authorization": f"Bearer {self.api_key}", "Idempotency-Key": idempotency_key,
                             "Content-Type": "application/json"},
                )
        except httpx.RequestError as exc:
            raise RetryableDeliveryError("transport_error") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if 200 <= response.status_code < 300:
            provider_id = body.get("id") if isinstance(body, dict) else None
            if not valid_message_id(provider_id):
                raise RetryableDeliveryError("invalid_response")
            return provider_id
        if response.status_code == 409:
            name = body.get("name") if isinstance(body, dict) else None
            if name == "invalid_idempotent_request":
                raise PermanentDeliveryError("idempotency_mismatch")
            raise RetryableDeliveryError("concurrent_request")
        if response.status_code == 429:
            raise RetryableDeliveryError("rate_limited")
        if response.status_code >= 500:
            raise RetryableDeliveryError("provider_unavailable")
        if response.status_code in (401, 403):
            raise PermanentDeliveryError("authentication_rejected")
        raise PermanentDeliveryError("request_rejected")


class ConsoleDelivery:
    def send(self, payload: dict, idempotency_key: str) -> str:
        print("Console delivery (no email sent):\n" + payload["text"])
        return "console-" + hashlib.sha256(idempotency_key.encode()).hexdigest()


def get_delivery():
    if settings.REVIEW_EMAIL_DELIVERY == "console":
        return ConsoleDelivery()
    if settings.REVIEW_EMAIL_DELIVERY == "resend":
        return ResendDelivery(settings.RESEND_API_KEY)
    raise ImproperlyConfigured("REVIEW_EMAIL_DELIVERY must be console or resend")
