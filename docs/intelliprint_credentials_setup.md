# Setting up your Intelliprint test API key

This is a standalone copy of the setup steps also written into
`.env.example.letter-fulfilment` (that file's own copy could not be
synced to your machine automatically this pass -- a device-tool
restriction blocks writing to any filename starting with `.env`, even a
template with no secrets in it, as a blanket safety measure. This
document has the same instructions and needs no such restriction).

**Do this in your Intelliprint account, not in chat with Claude. Never
paste your API key into a chat message.**

1. Log in at https://www.intelliprint.net (your login is already working
   again as of 2026-09-26).
2. Go to your account's API / Developer settings and generate a key.
   Intelliprint lets you create separate test and live keys "for clean
   isolation" if you want two -- either works for this project, because
   test-vs-live behaviour here is actually controlled by a separate
   `INTELLIPRINT_TEST_MODE` setting (Intelliprint's own `testmode`
   request parameter), not by which key you use.
3. In this project's root folder (the same folder as `main.py` and
   `UPDATE_WEBSITE.bat`), create a file named exactly `.env` if one
   doesn't already exist, and add this line:

   ```
   INTELLIPRINT_API_KEY=the-key-you-generated
   ```

   This project already loads `.env` automatically on startup, and
   `.env` / `.env.*` are already in `.gitignore` -- confirmed this
   pass -- so this file is never sent to GitHub or Render by
   `UPDATE_WEBSITE.bat` or anything else. It stays only on your machine.

4. Leave `INTELLIPRINT_TEST_MODE` unset (it defaults to `true`, meaning
   real postage/charging stays off) until you explicitly decide to go
   live. Real sending is also independently blocked by
   `LETTER_SENDING_LIVE` (currently unset/false) -- two separate
   switches both have to be turned on before any letter is really
   posted, and this pass didn't change either of them.

5. Once your `.env` has the key, double-click `RUN_INTELLIPRINT_TEST.bat`
   in this same folder to send one real test-mode submission to
   Intelliprint and see the result. This can never cost money or post a
   real letter -- test mode is locked on inside the script itself,
   regardless of anything in `.env`. Screenshot the window when it's
   done, the same way you already do for `UPDATE_WEBSITE.bat` -- it
   never prints your API key, so it's safe to share.
