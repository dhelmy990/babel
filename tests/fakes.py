"""In-memory delivery provider: no sockets, with idempotent acceptance."""
from copy import deepcopy
from threading import Lock


class FakeDelivery:
    def __init__(self, *, fail_after_accept=0, failures=()):
        self.calls = []
        self.messages = {}
        self.fail_after_accept = fail_after_accept
        self.failures = list(failures)
        self.lock = Lock()

    def send(self, payload, idempotency_key):
        with self.lock:
            self.calls.append((deepcopy(payload), idempotency_key))
            if self.failures:
                raise self.failures.pop(0)
            if idempotency_key in self.messages:
                saved, provider_id = self.messages[idempotency_key]
                assert saved == payload, "An idempotency key must never change payload"
            else:
                provider_id = f"fake-{len(self.messages) + 1}"
                self.messages[idempotency_key] = (deepcopy(payload), provider_id)
            if self.fail_after_accept:
                self.fail_after_accept -= 1
                raise TimeoutError("Sensitive provider response must not be recorded")
            return provider_id
