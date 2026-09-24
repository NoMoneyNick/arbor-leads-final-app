"""
letter_providers/postworks_provider.py -- NOT IMPLEMENTED. Do not treat as
complete or usable.

Same status as intelliprint_provider.py: no official Postworks API
documentation was consulted in this session. is_configured() always returns
False; send() raises loudly rather than guessing an endpoint shape.

Before this can be implemented: obtain Postworks' current official API
documentation and a test account, then write this adapter against verified
current field names -- following the same pattern as stannp_provider.py.
"""
from __future__ import annotations

from letter_providers.base import LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities


class PostworksProvider(LetterProviderAdapter):
    name = "postworks"
    capabilities = ProviderCapabilities()

    def is_configured(self) -> bool:
        return False

    def send(self, request: LetterRequest) -> ProviderResult:
        raise NotImplementedError(
            "PostworksProvider is an unimplemented placeholder -- no verified API "
            "documentation was available in this session. Do not wire this to a guessed endpoint."
        )
