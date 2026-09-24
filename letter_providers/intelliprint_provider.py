"""
letter_providers/intelliprint_provider.py -- NOT IMPLEMENTED. Do not treat
as complete or usable.

No official Intelliprint API documentation was consulted in this session --
I have not fetched or verified their current API surface, authentication
method, or endpoint shape, and I'm not willing to invent one. is_configured()
always returns False, so the registry will never select this adapter; send()
raises loudly if something calls it anyway, rather than pretending to work.

Before this can be implemented: obtain Intelliprint's current official API
documentation and a test account, then write this adapter against verified
current field names -- following the same pattern as stannp_provider.py.
"""
from __future__ import annotations

from letter_providers.base import LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities


class IntelliprintProvider(LetterProviderAdapter):
    name = "intelliprint"
    capabilities = ProviderCapabilities()

    def is_configured(self) -> bool:
        return False

    def send(self, request: LetterRequest) -> ProviderResult:
        raise NotImplementedError(
            "IntelliprintProvider is an unimplemented placeholder -- no verified API "
            "documentation was available in this session. Do not wire this to a guessed endpoint."
        )
