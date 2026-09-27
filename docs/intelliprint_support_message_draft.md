# Draft message for Intelliprint support (only if needed)

Only send this if the new `RUN_INTELLIPRINT_AUTH_CHECK.bat` check also
fails, or if you want to raise it proactively. Send it yourself, from
your own Intelliprint account email, to **hello@intelliprint.net** --
nothing here identifies your API key, and nothing should be added to it
that does.

---

Subject: 403 Forbidden on POST /v1/prints (testmode=true) -- key/account permission?

Hi,

I'm testing the Intelliprint API integration (testmode=true, so no
charge or real postage should be involved). A call to POST /v1/prints
is returning HTTP 403 Forbidden. Per your own docs
(intelliprint.net/docs/errors), I understand `code: "forbidden"` on a
403 means the key isn't authorised for the requested resource, as
opposed to a bad/missing key (401).

Could you confirm whether my account or API key needs any additional
activation, permission, or setup step (e.g. a payment method on file,
a plan restriction, or an account activation step) before it can
create print jobs via the API, even in test mode?

[If you've since run RUN_INTELLIPRINT_AUTH_CHECK.bat, paste its
"Detail:" line here -- it's already safe to share, with no key or
personal data in it.]

Thanks,
[your name]

---

**Do not paste your API key, or any Authorization header value, into
this message or anywhere else outside your own Intelliprint account
settings.**
