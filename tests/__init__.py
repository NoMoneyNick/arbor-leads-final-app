import os

# 2026-09-22 handoff: suppression.py's is_suppressed/add_suppression now
# require SUPPRESSION_HASH_KEY to be set (fail loud, not safe-and-silent --
# see suppression.py::_hash_key's own docstring) -- a large number of
# existing tests across several files drive letter_providers/registry.py's
# attempt_send, which calls suppression.is_suppressed as one step among
# several they're not actually testing the content of. Rather than
# threading a fixed test key through each of those files individually,
# set one default here, once, for the whole `unittest discover` run (this
# __init__.py is imported before any test module is collected) -- it is
# NOT a real secret, just a fixed non-empty string so hashing is
# deterministic and no test needs to know or care that suppression.py
# hashes anything at all. Tests that specifically exercise the hashing
# behaviour itself (tests/test_suppression.py) still set/clear this
# explicitly around their own cases, same as before.
os.environ.setdefault("SUPPRESSION_HASH_KEY", "unit-test-suppression-hash-key-not-a-real-secret")
