"""
letter_providers/fake_provider.py -- A genuinely working test/dev adapter.

Not a stub: this fully implements the adapter interface, is deterministic,
has no network dependency, and is exercised by tests/test_providers.py
(run locally this session -- see the test run output in the handoff). Use
FAKE_PROVIDER_FORCE_OUTCOME to script specific scenarios in tests
(accepted/rejected/unknown/timeout), so the registry's fallback logic can be
tested against every outcome without a real provider account.
"""
from __future__ import annotations

import os
import random

from letter_providers.base import (
    LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities,
    OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN,
)


class FakeLetterProvider(LetterProviderAdapter):
    name = "fake_test"
    capabilities = ProviderCapabilities(max_pages=2, countries=("GB",), max_cost_pence=200)

    def __init__(self, *, force_outcome: str = None, reject_postcodes: tuple = (), seed: int = 0):
        """force_outcome: None (normal -- always accepts valid GB requests),
        or one of 'accepted'/'rejected'/'unknown' to script a scenario.
        reject_postcodes: postcodes that should always be reported rejected,
        for testing suppressed/invalid-address handling end to end.

        2026-09-18 review, Section 1: an explicit constructor argument
        always wins; when NOT given one, this now actually reads
        FAKE_PROVIDER_FORCE_OUTCOME / FAKE_PROVIDER_REJECT_POSTCODES
        (comma-separated) from the environment, which is what this
        docstring already claimed before this fix -- the `import os` was
        present but unused, so the env vars it documented had no effect.
        This is what lets letter_providers.registry.build_registry_from_env
        (LETTER_PROVIDER_PRIMARY=fake_test, say) script a specific scenario
        purely from configuration, with no Python-level test wiring."""
        self.force_outcome = force_outcome if force_outcome is not None else (
            os.getenv("FAKE_PROVIDER_FORCE_OUTCOME", "").strip().lower() or None
        )
        if reject_postcodes:
            self.reject_postcodes = set(reject_postcodes)
        else:
            _env_rejects = os.getenv("FAKE_PROVIDER_REJECT_POSTCODES", "").strip()
            self.reject_postcodes = {p.strip().upper() for p in _env_rejects.split(",") if p.strip()}
        self._rng = random.Random(seed)
        self._sent_keys = {}  # idempotency_key -> ProviderResult, for duplicate-submit detection in tests

    def is_configured(self) -> bool:
        return True  # always available -- it's the safe default for dev/test

    def send(self, request: LetterRequest) -> ProviderResult:
        # Idempotency at the provider layer too, mirroring what a real
        # provider with an idempotency key would do -- lets tests assert
        # that a duplicate submit of the same key never double-charges/
        # double-sends even if the registry's own claim logic were bypassed.
        if request.idempotency_key in self._sent_keys:
            return self._sent_keys[request.idempotency_key]

        postcode = (request.address_lines.get("postcode") or "").strip().upper()
        if self.force_outcome == "unknown":
            result = ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                     message="Forced unknown outcome (simulated timeout).")
        elif self.force_outcome == "rejected" or postcode in self.reject_postcodes:
            result = ProviderResult(outcome=OUTCOME_REJECTED, provider_name=self.name,
                                     message="Forced/simulated rejection (e.g. invalid address).")
        elif self.force_outcome == "dispatched":
            # 2026-09-23, Request D Part 2 ("use a fake provider to test
            # dispatch events... now; real postal credentials are not
            # required for that work"): a real provider practically never
            # confirms full DISPATCH synchronously in its send() response
            # (see letter_providers/base.py's own OUTCOME_ACCEPTED vs
            # OUTCOME_DISPATCHED distinction) -- this branch exists purely
            # so tests can script "this send is immediately a confirmed
            # dispatch" in one call, without also needing the separate
            # simulate_dispatch_confirmed()+check_status() round trip below
            # for tests that don't care about that distinction. Still never
            # used by any real (non-test) code path -- nothing here invents
            # a live provider's dispatch signal, it only ever answers for
            # THIS fake adapter, which nothing in production selects.
            ref = f"FAKE-{self._rng.randint(100000, 999999)}"
            result = ProviderResult(outcome=OUTCOME_DISPATCHED, provider_name=self.name,
                                     provider_reference=ref, cost_pence=45,
                                     message="Simulated dispatch confirmation.")
        elif self.force_outcome == "accepted" or self.force_outcome is None:
            ref = f"FAKE-{self._rng.randint(100000, 999999)}"
            result = ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name=self.name,
                                     provider_reference=ref, cost_pence=45,
                                     message="Simulated acceptance.")
        else:
            raise ValueError(f"Unknown force_outcome {self.force_outcome!r}")

        self._sent_keys[request.idempotency_key] = result
        return result

    def simulate_dispatch_confirmed(self, provider_reference: str) -> bool:
        """Test-only helper (2026-09-23, Request D Part 2): advances a
        previously-'accepted' send to 'dispatched', the way a real
        provider's OWN later status update would -- send() and check_status()
        are the only two methods the adapter interface actually promises
        (LetterProviderAdapter), and a real provider's initial send()
        response is not where a genuine dispatch confirmation would appear
        anyway (see OUTCOME_DISPATCHED's own docstring: "the item has left
        their system" -- necessarily a later event than acceptance). Lets a
        test drive the realistic two-step "accepted now, dispatched later,
        discovered via check_status" flow without reaching into this
        adapter's private state directly. Returns False (no-op) if
        provider_reference isn't one this adapter has an 'accepted' result
        for on record."""
        for key, result in self._sent_keys.items():
            if result.provider_reference == provider_reference and result.outcome == OUTCOME_ACCEPTED:
                self._sent_keys[key] = ProviderResult(
                    outcome=OUTCOME_DISPATCHED, provider_name=self.name,
                    provider_reference=provider_reference, cost_pence=result.cost_pence,
                    message="Simulated dispatch confirmation (via check_status).",
                )
                return True
        return False

    def check_status(self, provider_reference: str):
        for result in self._sent_keys.values():
            if result.provider_reference == provider_reference:
                return result
        return None
