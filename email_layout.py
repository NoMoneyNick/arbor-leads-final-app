"""
email_layout.py -- the shared TreeKey email design (approved "forest + gold" email design).

WHAT IT IS
  Email programs ignore most modern CSS, so everything here is plain <table> markup with inline
  styles (no <style> blocks, no flexbox, no external CSS). One function builds the whole email:

      email_layout.render(heading="...", body_html="...", eyebrow="...", footer_note_html="...")

  plus small building blocks for the inside of an email: p(), button(), detail_box(), panel(),
  code_box(), note(), unsubscribe_html(). Outreach (plain, personal, first-touch) emails use
  render_outreach(), which is deliberately restrained: a thin forest-green band, plain copy,
  and a small crest in the signature.

WHAT IT DOES NOT DO
  It never sends anything and never decides content. notifications.py / main.py still build the
  real text, links, headers (List-Unsubscribe), sender and recipient exactly as before and just
  hand their body to render().

WHERE TO CHANGE THINGS
  Colours, tagline, footer wording, crest picture -> brand.py
  Spacing / fonts of every email                   -> the constants at the top of this file
  A single email's content                         -> the function that builds it (notifications.py)
"""
import html as _html
from typing import Iterable, Optional, Tuple

import brand

C = brand.COLORS
SERIF = "Georgia, 'Times New Roman', Times, serif"
SANS = "Arial, Helvetica, sans-serif"
MAX_WIDTH = 600
PAD_X = 36          # left/right padding inside the white paper (px)

# Sender identity for first-touch outreach. NOT final: Nick wants to replace the "Nick" identity
# before launch (name/address/signature still to be chosen), so it lives in these two constants
# only -- change it here, nowhere else.
OUTREACH_SIGNATURE_NAME = "Nick — TreeKey"
OUTREACH_SIGNATURE_LINK_LABEL = "treekey.co.uk"
OUTREACH_SIGNATURE_LINK_URL = "https://treekey.co.uk"


def _e(s) -> str:
    return _html.escape(str(s), quote=True)


# --------------------------------------------------------------------------- building blocks
def p(inner_html: str, *, last: bool = False) -> str:
    return (f'<p style="margin:0 0 {0 if last else 18}px 0;font-family:{SANS};font-size:15px;'
            f'line-height:1.65;color:{C["ink"]};">{inner_html}</p>')


def note(inner_html: str) -> str:
    """Small muted print under the main content."""
    return (f'<p style="margin:18px 0 0 0;font-family:{SANS};font-size:12px;line-height:1.6;'
            f'color:{C["muted"]};">{inner_html}</p>')


def eyebrow(text: str, *, on_dark: bool = False) -> str:
    colour = C["gold"] if on_dark else C["gold_text"]
    return (f'<div style="font-family:{SANS};font-size:11px;letter-spacing:1.4px;font-weight:bold;'
            f'color:{colour};text-transform:uppercase;">{_e(text)}</div>')


def _href(url: str) -> str:
    """Link target for an href: keeps '&' as it was before the rebrand (so every existing link and its
    tests are byte-identical) and only neutralises characters that could break out of the attribute."""
    return str(url).replace('"', "%22").replace("<", "%3C").replace(">", "%3E")


def button(label: str, url: str) -> str:
    """Bulletproof button (works in Outlook too): a coloured table cell with a padded link."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 4px 0;"><tr>'
        f'<td bgcolor="{C["gold"]}" style="background:{C["gold"]};border:1px solid {C["gold_line"]};border-radius:6px;">'
        f'<a href="{_href(url)}" style="display:inline-block;padding:16px 26px;font-family:{SERIF};font-size:16px;'
        f'font-weight:bold;color:#142b20;text-decoration:none;">{_e(label)}&nbsp; &rarr;</a>'
        '</td></tr></table>'
    )


def detail_box(inner_html: str) -> str:
    """Light box for key facts (the lead details etc.)."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px 0;"><tr>'
        f'<td bgcolor="{C["detail_bg"]}" style="background:{C["detail_bg"]};border:1px solid {C["footer_line"]};'
        f'border-radius:8px;padding:16px 18px;font-family:{SANS};font-size:15px;line-height:1.6;color:{C["ink"]};">'
        f'{inner_html}</td></tr></table>'
    )


def panel(inner_html: str, *, eyebrow_text: str = "", title: str = "") -> str:
    """Dark green feature panel (gold edge)."""
    head = ""
    if eyebrow_text:
        head += eyebrow(eyebrow_text, on_dark=True)
    if title:
        head += (f'<div style="font-family:{SERIF};font-size:24px;line-height:1.25;color:{C["ivory_text"]};'
                 f'margin:8px 0 10px 0;">{_e(title)}</div>')
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 24px 0;"><tr>'
        f'<td bgcolor="{C["forest_panel"]}" style="background:{C["forest_panel"]};border:1px solid {C["gold_line"]};'
        f'border-radius:8px;padding:22px 24px;font-family:{SANS};font-size:15px;line-height:1.6;color:#e2e8df;">'
        f'{head}{inner_html}</td></tr></table>'
    )


def code_box(label: str, code: str) -> str:
    """The big confirmation / sign-in code."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px 0;"><tr>'
        f'<td align="center" bgcolor="{C["forest_panel"]}" style="background:{C["forest_panel"]};border:1px solid {C["gold_line"]};'
        'border-radius:8px;padding:18px 16px;">'
        f'<div style="font-family:{SANS};font-size:12px;font-weight:bold;letter-spacing:1px;color:{C["gold"]};'
        f'margin:0 0 6px 0;">{_e(label)}</div>'
        f'<div style="font-family:\'Courier New\',monospace;font-size:28px;font-weight:bold;letter-spacing:3px;'
        f'color:{C["ivory_text"]};">{_e(code)}</div></td></tr></table>'
    )


def unsubscribe_html(url: str, label: str = "Unsubscribe") -> str:
    """Existing unsubscribe link (the real URL + the List-Unsubscribe headers are set by the caller)."""
    if not url:
        return ""
    return (f'<p style="margin:10px 0 0 0;font-family:{SANS};font-size:11px;line-height:1.6;color:{C["faint"]};">'
            f'<a href="{_href(url)}" style="color:{C["faint"]};text-decoration:underline;">{_e(label)}</a></p>')


def _preheader(text: str) -> str:
    if not text:
        return ""
    # Hidden preview text (what the inbox list shows next to the subject).
    return ('<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;'
            f'color:{C["ivory"]};">{_e(text)}' + "&nbsp;&zwnj;" * 40 + "</div>")


def _doc(title: str, inner: str, preheader: str = "") -> str:
    return (
        '<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">'
        f'<title>{_e(title)}</title></head>'
        f'<body style="margin:0;padding:0;background:#ffffff;">{_preheader(preheader)}{inner}</body></html>'
    )


# --------------------------------------------------------------------------- full layouts
def render(*, heading: str, body_html: str, eyebrow_text: str = "", footer_note_html: str = "",
           footer_links: Optional[Iterable[Tuple[str, str]]] = None, preheader: str = "",
           title: str = "", pad_x: int = PAD_X) -> str:
    """Branded transactional email: crest + wordmark header, content, footer.
    `heading` is escaped; `body_html`, `footer_note_html` are trusted HTML built by the caller
    (callers must already have escaped any user-supplied text, exactly as they did before)."""
    base = brand.public_base()
    links = footer_links if footer_links is not None else (("Help", base + "/faq"), ("Privacy", base + "/privacy-policy"))
    link_html = " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{_e(u)}" style="color:#47634e;text-decoration:underline;">{_e(t)}</a>' for t, u in links)
    crest = brand.email_crest_url()
    head_row = (
        f'<tr><td bgcolor="{C["forest_header"]}" style="background:{C["forest_header"]};padding:28px 34px;'
        f'border-bottom:2px solid {C["gold_line"]};border-radius:11px 11px 0 0;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td valign="middle" width="66" style="width:66px;"><img src="{_e(crest)}" width="66" height="61" '
        f'alt="{_e(brand.NAME)} crest" style="display:block;border:0;width:66px;height:61px;"></td>'
        '<td valign="middle" style="padding-left:16px;">'
        f'<div style="font-family:{SERIF};font-size:36px;line-height:1.1;color:{C["ivory_text"]};">{_e(brand.NAME)}</div>'
        f'<div style="font-family:{SANS};font-size:10px;letter-spacing:1.6px;color:{C["gold"]};margin-top:7px;">'
        f'{_e(brand.TAGLINE)}</div></td></tr></table></td></tr>'
    )
    eb = (eyebrow(eyebrow_text) + '<div style="height:12px;line-height:12px;">&nbsp;</div>') if eyebrow_text else ""
    content_row = (
        f'<tr><td style="padding:32px {pad_x}px 30px {pad_x}px;font-family:{SANS};font-size:15px;line-height:1.6;color:{C["ink"]};">{eb}'
        f'<h1 style="margin:0 0 20px 0;font-family:{SERIF};font-size:30px;line-height:1.2;font-weight:normal;'
        f'color:{C["heading"]};">{_e(heading)}</h1>{body_html}</td></tr>'
    )
    foot_row = (
        f'<tr><td bgcolor="{C["footer_bg"]}" style="background:{C["footer_bg"]};padding:23px {pad_x}px;'
        f'border-top:1px solid {C["footer_line"]};border-radius:0 0 11px 11px;font-family:{SANS};color:#5e6c61;">'
        f'<div style="font-family:{SERIF};font-size:18px;font-weight:bold;color:#183b2b;">{_e(brand.NAME)}</div>'
        f'<p style="margin:8px 0;font-size:11px;line-height:1.6;">{_e(brand.FOOTER_TAGLINE)}</p>'
        f'<p style="margin:8px 0;font-size:11px;line-height:1.6;">{_e(brand.OPERATOR_LINE)}</p>'
        f'<p style="margin:8px 0 0 0;font-size:11px;line-height:1.6;">{link_html}</p>{footer_note_html}</td></tr>'
    )
    card = (
        f'<table role="presentation" width="{MAX_WIDTH}" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:{MAX_WIDTH}px;border-collapse:separate;border:1px solid #a8894b;'
        f'border-radius:12px;background:{C["ivory"]};">{head_row}{content_row}{foot_row}</table>'
    )
    outer = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff">'
             f'<tr><td align="center" style="padding:16px 8px;">{card}</td></tr></table>')
    return _doc(title or heading, outer, preheader)


def render_outreach(*, body_html: str, unsubscribe_url: str = "", preheader: str = "", title: str = "") -> str:
    """Restrained first-touch (cold outreach) layout: thin forest band, plain personal copy,
    small crest + name in the signature. `body_html` is the caller's own paragraphs (use p())."""
    crest = brand.email_crest_url()
    sig = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:25px;border-top:1px solid {C["gold_line"]};"><tr>'
        '<td valign="middle" width="48" style="width:48px;padding-top:17px;">'
        f'<img src="{_e(crest)}" width="40" height="37" alt="{_e(brand.NAME)}" style="display:block;border:0;"></td>'
        f'<td valign="middle" style="padding-top:17px;font-family:{SANS};font-size:14px;line-height:1.5;color:{C["heading"]};">'
        f'{_e(OUTREACH_SIGNATURE_NAME)}<br>'
        f'<a href="{_e(OUTREACH_SIGNATURE_LINK_URL)}" style="font-size:12px;color:{C["muted"]};">'
        f'{_e(OUTREACH_SIGNATURE_LINK_LABEL)}</a></td></tr></table>'
    )
    band = (f'<tr><td height="7" bgcolor="{C["forest"]}" style="height:7px;line-height:7px;font-size:0;'
            f'background:{C["forest"]};border-bottom:2px solid {C["gold_line"]};border-radius:8px 8px 0 0;">&nbsp;</td></tr>')
    unsub = unsubscribe_html(unsubscribe_url).replace("margin:10px 0 0 0", "margin:25px 0 0 0")
    body = (f'<tr><td style="padding:30px {PAD_X}px 28px {PAD_X}px;font-family:{SANS};font-size:15px;line-height:1.65;'
            f'color:{C["ink"]};">{body_html}{sig}{unsub}</td></tr>')
    card = (f'<table role="presentation" width="{MAX_WIDTH - 40}" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:{MAX_WIDTH - 40}px;border-collapse:separate;border:1px solid #b39350;'
            f'border-radius:9px;background:{C["ivory"]};">{band}{body}</table>')
    outer = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff">'
             f'<tr><td align="center" style="padding:12px 8px;">{card}</td></tr></table>')
    return _doc(title or brand.NAME, outer, preheader)
