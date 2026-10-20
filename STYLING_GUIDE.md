# TreeKey page styling guide (remaining pages)

The homepage (`/`) and marketplace (`/marketplace`) are NOT touched by any of this.

## How it works (one paragraph)
Each route builds its page exactly as before. For the pages listed in `page_theme.py`, a small middleware in `main.py` (`_tk_page_theme`) re-wraps the finished HTML: adds `tk-v2` to `<body>`, swaps the old header/footer for the new ones, and links `static/treekey-pages.css`. Routes, forms, login, payments and wording are not changed.

## Where to change things

| I want to change... | Edit this |
|---|---|
| Colours, fonts, spacing, gaps, corner radius, button gold, page width | `static/treekey-pages.css`, **section 1 (tokens)** - the `--tk-...` variables at the top. Change one value, every themed page follows. |
| Cards, buttons, form fields, notices, badges, tables | `static/treekey-pages.css`, **section 4 (components)** |
| Dashboard "Quick links" tiles | CSS **section 5**; content/order in `main.py` -> `free_dashboard` -> the `quick_cards` list; markup in `_quick_links_html()` |
| Letter preview size/scroll | CSS **section 6**; markup in `main.py` -> `letter_settings_preview`; scaling script `_LETTER_PREVIEW_FIT_SCRIPT` |
| Header (logo, nav links, login/account area) | `main.py` -> `_v2_header_html()` |
| Footer (links, wording) | `main.py` -> `_v2_footer_html()` |
| Mobile/narrow-screen rules | CSS **section 9** |
| Page-specific tweaks | CSS **section 8** (one block per page, keep them there) |
| Which pages get the theme | `page_theme.py` -> `V2_PATHS` / `V2_PREFIXES` |
| Order of sections on a page | The page's own code in `main.py` (each section is a block in that route's HTML); the dashboard tiles follow the `quick_cards` list order |

## After editing the CSS
Change the number in `page_theme.py` -> `STYLESHEET_HREF` (`?v=2` -> `?v=3`) so browsers reload it.

## Switch it all off
Set the environment variable `TREEKEY_PAGE_THEME=off` and restart: every page renders as it did before.

## Rules to keep it tidy
- Add new colours/spaces as a `--tk-` token first, then use the token.
- Don't add inline `style="..."` for things a shared class already does.
- New page: add its path to `V2_PATHS`, use the shared classes (`tk-card`, `tk-btn`...) and put any one-off rules in CSS section 8.
- The old pages used Tailwind classes; section 7 of the CSS maps those to the new look so existing pages don't need rewriting. Over time, replace them with shared classes.

## Not covered by this guide
Printed letters/flyers/quotes, emails, and favicon/app icons are separate work.
