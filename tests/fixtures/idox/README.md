# tests/fixtures/idox/ — saved-page fixture format

**2026-09-22, second Astra review (relayed by Nick).** This directory holds
saved HTTP responses used to test the response classifier
(`net_utils.py`'s upcoming classification function — see `ERROR_LOG.md`)
and, later, once `mesh_scrapers.py` actually exists in this repo, real
Idox parser regression tests.

**Read this before adding anything here — Astra's own correction, twice
stated:** a fixture test proves "does this saved page still classify/parse
the way it did when captured." It does **not** prove a council's *live*
site still looks like that today. Live health checks (the existing
`system_warnings` / daily-digest infrastructure) remain necessary — this
directory is a regression net under that, not a replacement for it.

## Directory layout

```
tests/fixtures/idox/<council>/<case_name>/
    body.html      -- the raw response body, byte-for-byte as received
    meta.json       -- request/response metadata (schema below)
    expected.json    -- the ground truth a test asserts against
```

`<council>` is either a real council name (once a real capture exists) or
the literal `_synthetic` (see "Synthetic fixtures" below — never mix real
council names with fabricated content). `<case_name>` is a short
descriptive slug, e.g. `search_results_page_1`, `rate_limited_429`.

## meta.json schema

```json
{
  "fixture_version": 1,
  "provenance": "captured",
  "captured_at": "2026-09-22T00:00:00Z",
  "platform": "idox",
  "council": "Bristol",
  "page_kind": "search_results",
  "capture_type": "classifier_fixture",
  "request": {
    "method": "GET",
    "url": "https://example-council.public-i.tbmservices.com/online-applications/search.do",
    "search_parameters": {"searchType": "Application", "week": "2026-09-15"}
  },
  "response": {
    "final_url": "https://example-council.public-i.tbmservices.com/online-applications/search.do",
    "status": 200,
    "headers": {"Content-Type": "text/html; charset=UTF-8"},
    "redirects": [],
    "encoding": "utf-8",
    "tls_verified": true
  },
  "sanitisation": {
    "applied": false,
    "notes": ""
  }
}
```

Field notes:
- `provenance`: `"captured"` for a real response saved via the
  `net_utils.py` capture hook (see `ERROR_LOG.md`'s entry on that), or
  `"synthetic"` for a hand-built fixture that was never a real council
  response (see below) -- this is the single field every reader/test can
  check to know which kind of trust to place in the fixture.
- `page_kind`: what the page actually is (`search_results`,
  `application_detail`, `error`, `challenge`, etc.) -- independent of
  `capture_type`, which is what the fixture is *for*
  (`classifier_fixture` today; `parser_regression_fixture` once real
  parser fallback tests exist).
- `response.headers`: only headers relevant to classification/parsing are
  kept (e.g. `Content-Type`, `cf-mitigated`, `Retry-After`) -- not a full
  verbatim dump, to keep fixtures small and avoid accidentally capturing
  anything sensitive in a header value.
- `sanitisation`: whether anything was redacted/altered from the raw
  capture (e.g. a cookie or session token stripped from `body.html`) and,
  if so, a short note of what and why. `applied: false` for a fixture
  that's already safe to store verbatim.

## expected.json schema

For a classifier fixture (all fixtures in this repo today, since no real
parser exists yet -- see the scope note below):

```json
{"classification": "RATE_LIMITED", "notes": "429 with Retry-After header"}
```

`classification` is one of the taxonomy values the net_utils.py classifier
recognises: `PAGE_OK`, `VALID_EMPTY`, `RATE_LIMITED`, `CHALLENGED`,
`AUTH_REQUIRED`, `SERVER_ERROR`, `UNRECOGNISED_PAGE` (see that function's
own docstring for what each means).

## Synthetic fixtures

`tests/fixtures/idox/_synthetic/` holds hand-built, clearly-labeled
fixtures for response shapes that are simple enough to construct honestly
without needing a real council capture -- an HTTP 429 with a
`Retry-After` header, a 503, a Cloudflare `cf-mitigated: challenge`
response. These are generic protocol-level signatures, not fabricated
Idox page content, and every one has `"provenance": "synthetic"` in its
`meta.json` so nothing here is ever mistaken for a real council response.

**What must never go here:** fabricated real-looking Idox search-results
or application-detail HTML. Building that would require guessing at
`mesh_scrapers.py`'s actual selectors and a real council's actual page
structure -- neither of which this repo has -- and a test that passes
against invented HTML proves nothing about whether the real parser (once
it exists) will work against a real page. That category of fixture can
only be built once `mesh_scrapers.py` and at least one real captured page
are available (see `ERROR_LOG.md`).

## Where real fixtures will come from

Once the `net_utils.py` capture hook (see `ERROR_LOG.md`, added same day
as this README) is turned on for a real scrape, genuine `body.html` +
`meta.json` pairs land in a council-named subdirectory automatically.
`expected.json` for those still has to be written by a human (or Claude,
reviewing the actual captured page) -- it is never auto-generated from the
capture itself, since the whole point is an independently-authored ground
truth to test the classifier/parser against.
