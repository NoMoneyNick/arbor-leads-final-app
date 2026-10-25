"""home_v2.py -- the new TreeKey homepage (20 Oct 2026). Pure rendering: main.py supplies the data.

Static design blocks come from the approved preview; every number, notice, link and offer on it is real data from main.py.
Set TREEKEY_HOME_V2=off in Render to go back to the previous homepage (also: /?design=old for one visit).
"""

SYMBOLS = '<svg class="symbols" aria-hidden="true"><defs>\n<linearGradient id="gold-ink" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ffe09a"/><stop offset=".45" stop-color="#edbd60"/><stop offset="1" stop-color="#b7802e"/></linearGradient>\n<symbol id="leaf" viewBox="0 0 70 80"><g fill="url(#gold-ink)" stroke="#edbd60" stroke-linejoin="round"><path d="M18 75C30 53 38 30 42 5" fill="none" stroke-width="2.7"/><path d="M38 24C28 19 30 7 42 2c5 12 1 19-4 22ZM33 39C18 35 15 23 18 16c13 5 18 13 15 23ZM28 54C12 52 7 42 9 34c15 2 22 10 19 20ZM23 67C9 66 3 59 5 51c11 1 19 8 18 16ZM37 33C41 20 53 15 62 18c-3 13-13 19-25 15ZM32 49C40 35 54 34 62 39c-7 12-18 16-30 10ZM26 63C36 53 49 53 56 59c-9 10-20 11-30 4Z" fill-opacity=".24" stroke-width="1.8"/><path d="m39 22 1-13m-8 27-10-14m5 28L13 39m8 24L9 55m31-26 16-8m-20 24 20-5M30 60l18-1" fill="none" stroke-width="1.1"/></g></symbol>\n<symbol id="tree" viewBox="0 0 70 80"><g stroke="url(#gold-ink)" stroke-linecap="round" stroke-linejoin="round"><path d="M30 73h19m-13-1 1-31m0 17L22 43m15 9 15-13m-15 5-7-13" stroke-width="3" fill="none"/><path d="M26 52C12 57 4 47 10 39 1 32 7 21 17 22c-1-11 9-17 17-12 6-12 21-6 21 5 11-1 18 11 10 18 7 11-2 22-14 19-6 7-19 7-25 0Z" fill="#edbd6021" stroke-width="2"/><path d="m14 30 10 2m-7 11 9-3m9-18 1 9m15-10-6 9m13 7-9 3" stroke-width="1.3"/></g></symbol>\n<symbol id="document" viewBox="0 0 70 80"><g fill="none" stroke="url(#gold-ink)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 68V8h31l13 14v35M46 8v15h13M24 29h25M24 39h25M24 49h15M24 59h9"/><path d="M35 75c8-8 15-16 24-27M41 66c-3-13 9-22 24-23-1 14-9 25-24 23Z" fill="#edbd6026"/></g></symbol>\n<symbol id="pin" viewBox="0 0 70 80"><g fill="none" stroke="url(#gold-ink)" stroke-width="2.4" stroke-linejoin="round"><path d="M36 7C13 7 11 30 23 45l13 15 13-16C64 25 56 7 36 7Z"/><circle cx="36" cy="25" r="7"/><path d="M18 75c12-5 26-12 40-24M29 70c-13-1-16-8-16-16 13 2 20 8 16 16Zm10-8c2-12 11-17 23-17-3 12-12 20-23 17Z" fill="#edbd6026"/></g></symbol>\n<symbol id="small-pin" viewBox="0 0 24 30"><path fill="url(#gold-ink)" d="M12 1C-1 1-1 15 5 21l7 8 7-8C25 14 25 1 12 1Zm0 6a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z"/></symbol>\n<symbol id="logo" viewBox="0 0 24 24"><path d="M12 2L7 10h3v4H8l4 8 4-8h-2v-4h3z" fill="none" stroke="#a7f3d0" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>\n<symbol id="stump" viewBox="0 0 70 80"><g fill="none" stroke="url(#gold-ink)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="34" cy="36" rx="19" ry="8" fill="#edbd6021"/><ellipse cx="34" cy="36" rx="11" ry="4.5"/><ellipse cx="34" cy="36" rx="4" ry="1.8"/><path d="M15 36v20c0 5 8 9 19 9s19-4 19-9V36"/><path d="M15 56l-8 9m47-9l9 9M26 64l-3 8m17-8l3 8"/><path d="M48 28c2-9 9-13 17-12-1 9-8 14-17 12Z" fill="#edbd6026"/></g></symbol>\n</defs></svg>'

HERO = '<main id="home"><div class="scene"><section class="hero"><div class="eyebrow"><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg>LOCAL TREE WORK. ON YOUR PATCH.</div><h1>Find your next<span>tree-work opportunity.</span></h1><p class="intro">Find proposed tree work near you, from crown reductions to felling and removals. Look through the details and choose what suits your crew and kit. We print and post your approved introduction letter, so interested homeowners can contact you directly.<br>Buy one introduction at a time. No subscription. Printing and postage included.</p><div class="actions"><a class="gold" href="/marketplace">Browse local opportunities <span class="arrow">→</span></a><a href="#how" class="text-link">How TreeKey works</a><div class="promise" role="note"><svg class="ornament" aria-hidden="true" viewBox="0 0 70 80"><use href="#leaf"/></svg><div><strong>Sold to one tree firm through TreeKey, then taken off sale.</strong><span>We won’t sell the same introduction to another firm. The homeowner decides whether to respond and who to appoint; other contractors may find the work independently.</span></div></div></div><p class="note"><i>i</i>Planning activity indicates potential work, not a confirmed job.</p></section>'

BENEFITS = '<section class="benefits" aria-label="Benefits"><article class="benefit"><svg class="ornament" aria-hidden="true"><use href="#pin"/></svg><div><h3>Work on your patch</h3><p>Browse proposed tree work in the areas you cover.</p></div></article><article class="benefit"><svg class="ornament" aria-hidden="true"><use href="#document"/></svg><div><h3>Your introduction, printed and posted</h3><p>Approve a letter introducing your business. We handle the printing and postage.</p></div></article><article class="benefit"><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg><div><h3>Choose what suits your crew</h3><p>Read the proposed work before deciding whether to introduce yourself. Interested homeowners contact you directly.</p></div></article></section></div>'

HOW = '<section id="how" class="section"><div class="eyebrow"><svg class="ornament" aria-hidden="true"><use href="#tree"/></svg>MADE FOR YOUR WORKING DAY</div><h2>You choose the work.<br>We handle the post.</h2><p class="section-lead">TreeKey brings public tree-work planning records together so you can find local opportunities and introduce your business by letter.</p><div class="process"><article><svg class="ornament" aria-hidden="true"><use href="#pin"/></svg><span class="step-kicker">Choose</span><h3>Find work that fits</h3><p>Browse the areas you cover and read the proposed work.</p></article><article><svg class="ornament" aria-hidden="true"><use href="#document"/></svg><span class="step-kicker">Approve</span><h3>Approve your introduction</h3><p>Add your business details and choose a wording style. Check and approve the preview once. We reuse your approved letter until you change your details.</p></article><article><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg><span class="step-kicker">Print and post</span><h3>Leave the post to us</h3><p>We print and post your approved letter. If the homeowner is interested, they contact you directly.</p></article></div><p class="journey-note">This is an introduction based on a public planning record, not a request for quotes through TreeKey. A reply or a job isn’t guaranteed.</p><div class="account-cta"><a class="gold" href="/free-account">Create a free account →</a><span>No card required to create an account.</span></div></section>'

LOCAL = '<section class="section local" id="area"><div class="local-copy"><div class="eyebrow">PROPOSED WORK NEAR YOU</div><h2>See what’s proposed<br>on your patch.</h2><p class="section-lead">Choose your area and distance to browse the available opportunities.</p></div><div class="local-row"><div class="local-image" role="img" aria-label="A tree surgeon cutting a felled log"><p>For the people<br>who care for our trees.</p></div><a class="gold" href="#area-map">Explore the map <span class="arrow">↓</span></a></div></section>'

PRICE = '<section class="section price-detail"><div class="price-copy"><div class="eyebrow">PAY FOR WHAT YOU CHOOSE</div><h2>One introduction at a time.<br>No subscription.</h2><p class="section-lead">See the price before you buy. Printing and postage are included.</p><a class="gold" href="/pricing">See prices and what’s included →</a></div><div class="price-panel"><div class="tier-grid" role="list" aria-label="Standard introduction prices"><div class="tier" role="listitem"><span class="tier-price">£19</span><p class="tier-desc" data-tier="19">Standard opportunities, after their freshest window.</p></div><div class="tier" role="listitem"><span class="tier-price">£29</span><p class="tier-desc" data-tier="29">Fresh Standard opportunities, or Priority ones after their freshest window.</p></div><div class="tier" role="listitem"><span class="tier-price">£39</span><p class="tier-desc" data-tier="39">Fresh Priority opportunities, or Elite ones after their freshest window.</p></div><div class="tier" role="listitem"><span class="tier-price">£49</span><p class="tier-desc" data-tier="49">Elite opportunities in their freshest window.</p></div></div><p class="price-common"><svg class="ornament" aria-hidden="true"><use href="#document"/></svg><span><b>Printing and postage included. No subscription.</b> Pay only for the introductions you choose.</span></p></div></section>'

ADVANTAGE = '<section class="section advantage"><div class="eyebrow">THE TREEKEY ADVANTAGE</div><h2>A clearer route to local work.</h2><div class="process"><article><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg><h3>Sold once through TreeKey</h3><p>Each introduction is sold to one tree firm through TreeKey, then taken off sale. You choose the work worth pursuing.</p></article><article><svg class="ornament" aria-hidden="true"><use href="#pin"/></svg><h3>Your area and beyond</h3><p>Explore your chosen radius and connected-area opportunities where coverage is available.</p></article><article><svg class="ornament" aria-hidden="true"><use href="#tree"/></svg><h3>Public planning insight</h3><p>Understand proposed tree work before deciding whether to introduce your business.</p></article></div></section>'

TOOLS_AND_FAQ = '<section class="section" id="tools"><div class="eyebrow">BEYOND THE NEXT JOB</div><h2>Useful tools for the working day</h2><div class="tool-grid"><article class="tool-card"><img src="/static/images/tool-ledger.jpg" alt="TreeKey Ledger" loading="lazy"><div><svg class="ornament" aria-hidden="true"><use href="#document"/></svg><h3>Keep an eye on the figures</h3><p>Keep costs and turnover together, with a clearer view of the figures behind your quotes.</p><a class="text-link" href="/login">Open Ledger →</a></div></article><article class="tool-card"><img src="/static/images/tool-chip-drop.jpg" alt="Chip-Drop Network" loading="lazy"><div><svg class="ornament" aria-hidden="true"><use href="#tree"/></svg><h3>Find a woodchip or timber tip site</h3><p>Browse registered drop spots and check access, accepted materials and arrangements before heading over.</p><a class="text-link" href="/chip-drop">Explore Chip-Drop →</a></div></article><article class="tool-card"><img src="/static/images/tool-storm-radar.jpg" alt="Storm Alerts" loading="lazy"><div><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg><h3>Keep an eye on the weather</h3><p>View weather warnings alongside your other TreeKey tools.</p><a class="text-link" href="/storm-radar">View weather alerts →</a></div></article><article class="tool-card"><img src="/static/images/tool-app.jpg" alt="TreeKey on your phone" loading="lazy"><div><svg class="ornament" aria-hidden="true"><use href="#pin"/></svg><h3>Keep TreeKey to hand</h3><p>Open your opportunities and tools from your phone’s home screen.</p><a class="text-link" href="/login">Explore the app →</a></div></article></div></section><section class="faq" id="questions"><h2>A few things worth knowing.</h2><details><summary>What is TreeKey?</summary><p>TreeKey brings together proposed tree work from public planning records. You choose an opportunity and approve a letter introducing your business. We print and post it to the homeowner, who can contact you directly if interested.</p></details><details><summary>What does “sold once” mean?</summary><p>Once you buy an introduction, we take it off sale and won’t sell it to another firm through TreeKey. It doesn’t mean other contractors cannot find or quote for the work independently.</p></details><details><summary>How much does it cost?</summary><p>Standard introductions start from £19 and vary by freshness and value. Printing and postage are included. There is no subscription. Eligible businesses can try a selected Standard introduction for £4.99.</p></details><details><summary>When does new work appear?</summary><p>Tree-related planning notices are listed as they are processed from public planning sources. Coverage and availability can vary.</p></details><details><summary>Do I need a subscription?</summary><p>No. You pay for each introduction you choose.</p></details><a class="text-link" href="/faq">Read the full FAQ →</a></section></main>'


import html as _html
import re as _re

_ICON_RULES = (
    (_re.compile(r"crown\s*(reduc|thin|lift)|\bcanopy\b|\bthinning\b|\blifting\b", _re.I), "tree"),
    (_re.compile(r"prun|hedge|trim|cut\s*back", _re.I), "leaf"),
    (_re.compile(r"remov|fell|dismantl|stump|dead|dangerous", _re.I), "stump"),
    (_re.compile(r"protect|tpo|conservation|preservation", _re.I), "tree"),
)


def _icon_id(job_type: str) -> str:
    for rx, sym in _ICON_RULES:
        if rx.search(job_type or ""):
            return sym
    return "leaf"


def _e(value) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def _feed_rows_html(feed) -> str:
    if not feed:
        return ('<div class="lead"><div class="lead-title"><span class="lt">New notices are being processed.</span></div></div>')
    out = []
    for r in feed:
        title, area, size = _e(r.get("title")), _e(r.get("area")), _e(r.get("size"))
        out.append(
            '<div class="lead"><div class="lead-title">'
            f'<svg class="ornament" aria-hidden="true" focusable="false"><use href="#{_icon_id(r.get("title"))}"/></svg><span class="lt">{title}</span></div>'
            f'<span class="location"><svg class="ornament" aria-hidden="true"><use href="#small-pin"/></svg>{area}</span>'
            f'<em class="size-tag">{size}</em>'
            '<span class="source"><svg class="ornament" aria-hidden="true"><use href="#document"/></svg>Planning application</span>'
            f'<a class="row-link" href="/marketplace" aria-label="View in the marketplace: {title}, {area}">View details →</a></div>'
        )
    return "".join(out)


def _header_html(auth) -> str:
    if auth:
        account = (f'<a href="{_e(auth.get("dashboard_url") or "/dashboard")}">Dashboard</a>'
                   '<a class="outline" href="/logout">Log out</a>')
    else:
        account = '<a href="/login">Log in</a><a class="outline" href="/free-account">Create account</a>'
    return (
        '<header id="site-header"><a href="/" class="brand"><span class="chosen-logo">'
        '<img src="/static/images/logo-wreath.png" alt="TreeKey tree and key with gold wreaths"></span>TreeKey</a>'
        '<button class="outline mobile-toggle" type="button" aria-expanded="false" aria-controls="main-nav">Menu</button>'
        '<nav id="main-nav" aria-label="Main navigation"><a href="#how">How it works</a><a href="/marketplace">Proposed work</a>'
        '<a href="/pricing">Pricing</a><a href="#area-map">Map</a><a href="#tools">Tools</a><a href="/faq">FAQ</a></nav>'
        '<div class="account"><button id="pwaInstallBtn" class="install-link tk-hide" type="button" onclick="tkInstallApp()">Install app</button>'
        + account +
        '</div>'
        '<div id="pwaInstallTip" class="tk-hide" role="dialog" aria-label="Add TreeKey to your Home Screen">'
        '<button type="button" class="tip-close" aria-label="Close" onclick="document.getElementById(\'pwaInstallTip\').classList.add(\'tk-hide\')">×</button>'
        '<strong>Add TreeKey to your Home Screen</strong>'
        '<p>1. Tap the Share button (a square with an arrow). On newer iPhones it is under the \u2022\u2022\u2022 button at the bottom right of Safari.</p>'
        '<p>2. Scroll down and tap “Add to Home Screen”.</p><p>3. Tap “Add”.</p></div>'
        '</header>'
    )


def _leads_section(feed) -> str:
    return (
        '<section id="leads" class="opportunities glass-trial"><div class="section-top"><div class="title-wrap">'
        '<h2>Proposed work near you</h2><span class="tiny-label">FROM PUBLIC PLANNING NOTICES</span></div>'
        '<div class="pledge"><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg>Built for tree contractors'
        '<svg class="ornament right" aria-hidden="true"><use href="#leaf"/></svg></div></div>'
        '<p class="section-lead">See what’s listed in the areas you cover, then choose the work that fits your crew and kit.</p>'
        f'<div class="rows" id="feed-rows" aria-live="polite">{_feed_rows_html(feed)}</div></section>'
    )


def _activity_html(counts, total) -> str:
    def n(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "—"
    return (
        '<section class="activity-strip" aria-label="Planning activity"><div><span>Planning activity</span>'
        '<small>Tree-related planning notices recorded</small></div>'
        f'<div><strong id="countToday">{n(counts.get("today"))}</strong><span>Today</span></div>'
        f'<div><strong id="countWeek">{n(counts.get("week"))}</strong><span>This week</span></div>'
        f'<div><strong id="countMonth">{n(counts.get("month"))}</strong><span>This month</span></div>'
        f'<div><strong>{n(total)}</strong><span>Total notices recorded</span></div></section>'
        '<aside class="exclusive"><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg>'
        '<p><strong>Choose the work that suits you.</strong> The homeowner decides whether to respond.</p>'
        '<a class="text-link" href="/marketplace">Browse proposed work →</a></aside>'
    )


SOURCE_INFO = (
    '<section class="source-info" aria-label="Source information"><p class="provenance">Planning information is sourced '
    'directly from council and National Park planning registers and published under the Open Government Licence.</p>'
    '<img class="legacy-wordmark" src="/static/images/legacy-treekey-wordmark.png" alt="TreeKey" width="72" height="54"></section>'
)


def _offer_html(offer) -> str:
    if not offer:
        return ""
    return (
        '<section id="pricing" class="offer"><div><div class="eyebrow offer-eyebrow">NEW CUSTOMER OFFER</div>'
        f'<h2>Try your first introduction for <span>{offer["price"]}</span>.</h2>'
        '<p>Choose an eligible Standard opportunity and approve your letter. Printing and postage are included, with no subscription.</p>'
        '<small>Sign in and save your business details first. First purchase only, one per eligible business, on selected Standard opportunities. Not available to subscribers.</small></div>'
        f'<a class="gold" href="{_e(offer["href"])}">Find an introduction to try <span class="arrow">→</span></a></section>'
    )


MAP_SECTION = """<section id="area-map" class="homepage-map" aria-label="Explore tree-work opportunities">
<div class="tkmap-panel"><div class="tkmap-toolbar"><div class="tkmap-title"><svg class="ornament" aria-hidden="true"><use href="#leaf"/></svg><h3>Find proposed work near you</h3></div>
<form class="tkmap-form" id="areaForm" autocomplete="off"><div><label for="postcodeInput">Postcode or area</label><input id="postcodeInput" type="text" placeholder="e.g. Nottingham or NG22" maxlength="40" autocomplete="postal-code"></div>
<div><label for="radiusSelect">Distance</label><select id="radiusSelect"><option value="10">10 miles</option><option value="15" selected>15 miles</option><option value="20">20 miles</option><option value="25">25 miles</option></select></div>
<button class="gold" type="submit">Explore area <span class="arrow">→</span></button></form></div>
<div id="map" role="application" aria-label="Map of the area searched"></div>
<div class="tkmap-foot"><span id="areaStatus" aria-live="polite">Showing the whole of Great Britain. Enter a postcode or tap the map.</span><span>Approximate areas only</span></div></div>
<div class="local-results"><div class="results-heading"><h3>Proposed work in this area</h3><span id="area-result-count" aria-live="polite">Choose an area to see proposed work.</span></div>
<div class="table-scroll"><table class="results-table" role="table"><thead role="rowgroup"><tr role="row"><th role="columnheader">Notice</th><th role="columnheader">Approximate area</th><th role="columnheader">Scope</th><th role="columnheader">Published</th><th role="columnheader"><span class="sr-only">Details</span></th></tr></thead><tbody id="area-results" role="rowgroup"></tbody></table></div>
<p id="area-empty" hidden>No proposed work listed in this area at the moment. Try a wider distance.</p></div></section>"""


def _footer_html() -> str:
    return (
        '<footer><div class="foot-left"><a href="/" class="brand"><span class="chosen-logo"><img src="/static/images/logo-wreath.png" alt="TreeKey tree and key with gold wreaths"></span>TreeKey</a>'
        '<div><p>Printed introductions and practical tools for tree contractors.</p>'
        '<p class="footer-note">Planning information comes from public UK council planning registers.<br>© 2026 Tree Key. All rights reserved.<br>Contact: support@treekey.co.uk</p>'
        '<img class="legacy-tk" src="/static/images/legacy-tk-mark.png" alt="TK" width="34" height="26"></div></div>'
        '<nav aria-label="Footer"><a href="#how">How it works</a><a href="/marketplace">Marketplace</a><a href="/pricing">Packages</a>'
        '<a href="/storm-radar">Storm Watch</a><a href="/chip-drop">Chip-drop</a><a href="/faq">FAQ</a>'
        '<a href="mailto:support@treekey.co.uk">Help</a><a href="/suggestions">Suggestions</a><a href="/privacy-policy">Privacy</a>'
        '<a href="/terms-of-service">Terms</a><a href="/login">Login</a></nav></footer>'
    )


_HEAD = """<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TreeKey | UK Tree Surgery Planning Intelligence</title>
<meta name="description" content="Find tree-work opportunities from UK council planning notices. Choose one and TreeKey prints and posts your introduction to the homeowner. Each introduction is sold to one contractor only.">
<meta property="og:title" content="TreeKey | UK Tree Surgery Planning Intelligence">
<meta property="og:description" content="Find tree-work opportunities from UK council planning notices. Each introduction is sold to one contractor only; we print and post it for you.">
<meta property="og:url" content="https://treekey.co.uk">
<meta property="og:type" content="website">
<meta property="og:image" content="https://treekey.co.uk/static/images/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://treekey.co.uk/static/images/og-image.png">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#06291f">
<link rel="icon" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css">
<link rel="stylesheet" href="/static/home-v2.css?v=__ASSET__">
<script>if ('serviceWorker' in navigator) { window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js'); }); }</script>
</head>
<body>
"""

_SCRIPT = r"""
<script src="https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.js"></script>
<script src="/static/job-type-icons.js?v=__ASSET__"></script>
<script>
(function () {
  /* mobile menu */
  var toggle = document.querySelector('.mobile-toggle'), hdr = document.getElementById('site-header'), nav = document.getElementById('main-nav');
  function setMenu(on) { toggle.setAttribute('aria-expanded', on); nav.classList.toggle('expanded', on); hdr.classList.toggle('menu-open', on); }
  toggle.onclick = function () { setMenu(toggle.getAttribute('aria-expanded') !== 'true'); };
  nav.querySelectorAll('a').forEach(function (a) { a.addEventListener('click', function () { setMenu(false); }); });

  /* live counts: the same endpoint and pacing as before; only moves when a real notice lands */
  var todayEl = document.getElementById('countToday'), weekEl = document.getElementById('countWeek'), monthEl = document.getElementById('countMonth');
  function animateTo(el, v) {
    if (!el || typeof v !== 'number') return;
    var old = parseInt((el.textContent || '0').replace(/[^0-9]/g, ''), 10) || 0;
    if (v <= old) { el.textContent = v.toLocaleString('en-GB'); return; }
    var cur = old;
    (function step() { cur = Math.min(v, cur + [1, 1, 2, 2, 3][Math.floor(Math.random() * 5)]); el.textContent = cur.toLocaleString('en-GB'); if (cur < v) setTimeout(step, 200 + Math.random() * 500); })();
  }
  function pollCounts() { fetch('/api/live-counts').then(function (r) { return r.json(); }).then(function (d) { animateTo(todayEl, d.today); animateTo(weekEl, d.week); animateTo(monthEl, d.month); }).catch(function () {}); }
  function scheduleNext() { var h = new Date().getHours(), active = h >= 6 && h < 23; setTimeout(function () { if (active) pollCounts(); scheduleNext(); }, active ? (8000 + Math.random() * 22000) : 300000); }
  if (todayEl) scheduleNext();

  /* install app: computers get a QR code for their phone, Android gets the browser's own install prompt, iPhone gets the Safari steps */
  var btn = document.getElementById('pwaInstallBtn'), tip = document.getElementById('pwaInstallTip');
  var ua = navigator.userAgent;
  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
  var isIOS = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var isAndroid = /android/i.test(ua), isDesktop = !isIOS && !isAndroid && !/mobile/i.test(ua), deferred = null;
  if (btn && !standalone) {
    window.addEventListener('appinstalled', function () { btn.classList.add('tk-hide'); tip.classList.add('tk-hide'); deferred = null; });
    if (isDesktop) {
      /* a computer never gets an install prompt from us: point people to their phone */
      btn.textContent = 'Get TreeKey on your phone'; btn.style.whiteSpace = 'nowrap'; btn.classList.remove('tk-hide');
      window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); });
      window.tkInstallApp = function () {
        tip.innerHTML = '<button type="button" class="tip-close" aria-label="Close" onclick="document.getElementById(\'pwaInstallTip\').classList.add(\'tk-hide\')">\u00d7</button><strong>Get TreeKey on your phone</strong><p>Scan this with your phone camera to open the install page.</p><img src="/static/images/get-app-qr.png" alt="QR code to open treekey.co.uk/get-app on your phone" width="140" height="140" style="display:block;margin:8px auto 0;background:#fff;padding:6px;border-radius:8px">';
        tip.classList.toggle('tk-hide');
      };
    } else if (isIOS) {
      btn.classList.remove('tk-hide');
      var shown = false; try { shown = localStorage.getItem('tk_install_tip_shown') === '1'; } catch (e) {}
      if (!shown) setTimeout(function () { tip.classList.remove('tk-hide'); try { localStorage.setItem('tk_install_tip_shown', '1'); } catch (e) {} }, 1800);
      window.tkInstallApp = function () { tip.classList.toggle('tk-hide'); };
    } else {
      /* Android and other phone browsers: the button appears only when the browser says TreeKey can be installed */
      window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); deferred = e; btn.classList.remove('tk-hide'); });
      window.tkInstallApp = function () {
        if (!deferred) return;
        var d = deferred; deferred = null; d.prompt();
        d.userChoice.finally(function () { btn.classList.add('tk-hide'); });
      };
    }
  }

  /* the area map: same endpoint and behaviour as before (postcode or place search, radius, click to move) */
  if (typeof maplibregl === 'undefined') { var st = document.getElementById('areaStatus'); if (st) st.textContent = 'The map could not load. You can still browse the marketplace.'; return; }
  var centre = [-1.8904, 52.4862], radiusMiles = 15, ready = false;
  var map;
  try {
    map = new maplibregl.Map({ container: 'map', style: location.origin + '/static/home-v2/forest-map-style.json', center: centre, zoom: 8.6, maxZoom: 17, attributionControl: false });
  } catch (e) { var st2 = document.getElementById('areaStatus'); if (st2) st2.textContent = 'The map could not load on this device. You can still browse the marketplace.'; return; }
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.AttributionControl({ compact: false }));
  function ring() {
    var pts = [], r = radiusMiles * 1609.344 / 6371000, lat = centre[1] * Math.PI / 180, lng = centre[0] * Math.PI / 180;
    for (var i = 0; i <= 128; i++) {
      var a = i / 128 * 2 * Math.PI, y = Math.asin(Math.sin(lat) * Math.cos(r) + Math.cos(lat) * Math.sin(r) * Math.cos(a)),
          x = lng + Math.atan2(Math.sin(a) * Math.sin(r) * Math.cos(lat), Math.cos(r) - Math.sin(lat) * Math.sin(y));
      pts.push([x * 180 / Math.PI, y * 180 / Math.PI]);
    }
    return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [pts] } };
  }
  function fitRing() { var b = new maplibregl.LngLatBounds(); ring().geometry.coordinates[0].forEach(function (p) { b.extend(p); }); map.fitBounds(b, { padding: 30, duration: 900 }); }
  /* Decorative leaf foliage fixed to the land (geographic raster, moves and scales with the map). Not woodland data. */
  function addLandFoliage() {
    var art = new Image();
    art.onload = function () {
      var cv = document.createElement('canvas'); cv.width = 1200; cv.height = 1200;
      var cx = cv.getContext('2d'); cx.drawImage(art, 0, 0, 1200, 600); cx.drawImage(art, 0, 600, 1200, 600);
      map.addSource('land-botanical', { type: 'raster', tiles: [cv.toDataURL('image/png')], tileSize: 512, minzoom: 8, maxzoom: 8, bounds: [-11, 49, 3, 61] });
      var before = map.getStyle().layers.find(function (l) { return l.type === 'line' || l.type === 'symbol'; });
      map.addLayer({ id: 'land-botanical', type: 'raster', source: 'land-botanical', paint: { 'raster-opacity': 0.88, 'raster-fade-duration': 0 } }, before && before.id);
    };
    art.src = '/static/home-v2/map-botanical.svg?v=20261020';
  }
  /* every open lead is a pin (approximate area, never an address). Pins group into numbered circles when zoomed out; groups stay locked to the land. */
  function pinImage() {
    var c = document.createElement('canvas'); c.width = 54; c.height = 74; var x = c.getContext('2d'); x.scale(2, 2);
    x.beginPath(); x.moveTo(13.5, 1); x.bezierCurveTo(-1, 1, -2, 16, 5, 24); x.lineTo(13.5, 36); x.lineTo(22, 24); x.bezierCurveTo(29, 15, 28, 1, 13.5, 1); x.closePath();
    x.fillStyle = '#e8bf6c'; x.fill(); x.lineWidth = 1; x.strokeStyle = '#ffe4a6'; x.stroke();
    x.beginPath(); x.arc(13.5, 13, 4, 0, Math.PI * 2); x.fillStyle = '#15362d'; x.fill();
    return x.getImageData(0, 0, 54, 74);
  }
  function leadPopup(props) {
    var box = mk('div', 'tk-pop');
    box.appendChild(mk('h3', null, props.cat || 'Tree work'));
    box.appendChild(mk('p', 'tk-pop-area', props.oc + ' area'));
    box.appendChild(mk('p', 'tk-pop-note', 'Approximate location'));
    var a = mk('a', 'tk-pop-link', 'View opportunity →'); a.href = '/marketplace/lead/' + encodeURIComponent(props.id); box.appendChild(a);
    return box;
  }
  var leadsData = fetch('/api/map-leads').then(function (r) { return r.json(); });
  function addLeadLayers() {
    leadsData.then(function (d) {
      var feats = (d.leads || []).map(function (l) { return { type: 'Feature', properties: { id: l[0], cat: l[3], oc: l[4] }, geometry: { type: 'Point', coordinates: [l[2], l[1]] } }; });
      map.addImage('tk-pin', pinImage(), { pixelRatio: 2 });
      map.addSource('leads', { type: 'geojson', data: { type: 'FeatureCollection', features: feats }, cluster: true, clusterRadius: 48, clusterMaxZoom: 13 });
      map.addLayer({ id: 'lead-clusters', type: 'circle', source: 'leads', filter: ['has', 'point_count'],
        paint: { 'circle-color': '#0b2f22', 'circle-opacity': 0.96, 'circle-stroke-color': '#e8bf6c', 'circle-stroke-width': 2, 'circle-radius': ['step', ['get', 'point_count'], 16, 10, 19, 50, 23, 200, 28] } });
      map.addLayer({ id: 'lead-cluster-count', type: 'symbol', source: 'leads', filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular'], 'text-size': 14, 'text-allow-overlap': true },
        paint: { 'text-color': '#f4eddc' } });
      map.addLayer({ id: 'lead-pins', type: 'symbol', source: 'leads', filter: ['!', ['has', 'point_count']],
        layout: { 'icon-image': 'tk-pin', 'icon-anchor': 'bottom', 'icon-allow-overlap': true } });
      map.on('click', 'lead-clusters', function (e) {
        var f = e.features[0]; map.getSource('leads').getClusterExpansionZoom(f.properties.cluster_id).then(function (z) { map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.5 }); });
      });
      map.on('click', 'lead-pins', function (e) {
        var f = e.features[0];
        new maplibregl.Popup({ offset: [0, -34], maxWidth: 'min(290px, calc(100vw - 44px))', focusAfterOpen: false }).setLngLat(f.geometry.coordinates.slice()).setDOMContent(leadPopup(f.properties)).addTo(map);
      });
      ['lead-clusters', 'lead-pins'].forEach(function (id) {
        map.on('mouseenter', id, function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', id, function () { map.getCanvas().style.cursor = ''; });
      });
    }).catch(function () {});
  }
  map.on('load', function () {
    addLandFoliage();
    map.addSource('search-radius', { type: 'geojson', data: ring() });
    var label = map.getStyle().layers.find(function (l) { return l.type === 'symbol'; }).id;
    map.addLayer({ id: 'radius-fill', type: 'fill', source: 'search-radius', paint: { 'fill-color': '#d6b55d', 'fill-opacity': 0.11 } }, label);
    map.addLayer({ id: 'radius-outline', type: 'line', source: 'search-radius', paint: { 'line-color': '#e0c06a', 'line-width': 1.4 } }, label);
    ready = true; fitRing(); addLeadLayers();
  });
  function setRing() { if (ready) map.getSource('search-radius').setData(ring()); }
  var input = document.getElementById('postcodeInput'), radiusSel = document.getElementById('radiusSelect'), status = document.getElementById('areaStatus');
  var countEl = document.getElementById('area-result-count'), body = document.getElementById('area-results'), emptyEl = document.getElementById('area-empty');
  var token = 0;
  function mk(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function renderRows(notices) {
    body.replaceChildren();
    (notices || []).forEach(function (n) {
      var title = n.category || 'Tree work', tr = mk('tr'); tr.setAttribute('role', 'row');
      var c1 = mk('td', 'c-title'), nc = mk('span', 'notice-cell');
      if (window.TreeKeyJobIcons) nc.appendChild(TreeKeyJobIcons.create(title, 'ornament'));
      nc.appendChild(mk('span', null, title)); c1.appendChild(nc);
      var c2 = mk('td', 'c-area'); c2.appendChild(mk('span', 'a', (n.area_label || '') + ' area'));
      var c3 = mk('td', 'c-scope', n.size ? n.size + ' job' : 'Tree works');
      var c4 = mk('td', 'c-pub', [n.date, n.time].filter(Boolean).join(' · '));
      var c5 = mk('td', 'c-act'), a = mk('a', 'text-link', 'View in marketplace →'); a.href = '/marketplace'; c5.appendChild(a);
      [c1, c2, c3, c4, c5].forEach(function (c) { c.setAttribute('role', 'cell'); tr.appendChild(c); });
      body.appendChild(tr);
    });
    emptyEl.hidden = !!(notices && notices.length);
  }
  function show(data, miles) {
    centre = [data.lng, data.lat]; radiusMiles = miles; setRing();
    var inR = data.selected_area_leads || 0, near = data.connected_area_leads || 0;
    status.textContent = inR + (inR === 1 ? ' opportunity' : ' opportunities') + ' within ' + miles + ' miles of ' + data.postcode + (near ? ', plus ' + near + ' in connected areas' : '') + '.';
    if (data.council_source_issue) status.textContent += ' Note: ' + data.council_source_issue;
    countEl.textContent = (data.area_notices || []).length ? 'Latest in ' + data.postcode : 'None listed in ' + data.postcode;
    renderRows(data.area_notices);
  }
  function fail(msg) { status.textContent = msg; countEl.textContent = ''; renderRows([]); emptyEl.hidden = true; }
  async function scan(skipZoom, latlng) {
    var miles = parseInt(radiusSel.value, 10) || 15, mine = ++token;
    radiusMiles = miles; setRing();
    var url = latlng ? '/api/check-postcode?lat=' + latlng.lat + '&lng=' + latlng.lng + '&radius=' + miles : '/api/check-postcode?postcode=' + encodeURIComponent(input.value) + '&radius=' + miles;
    status.textContent = 'Searching…';
    try {
      var res = await fetch(url), data = await res.json();
      if (mine !== token) return;
      if (data.status === 'ok') { show(data, miles); if (!skipZoom && !latlng) fitRing(); }
      else if (data.status === 'out_of_bounds') fail(data.message || 'That is outside the area we cover.');
      else fail('We could not find that postcode or place. Try an outward code such as NG22.');
    } catch (e) { if (window.console) console.error('TreeKey map search error', e); if (mine === token) fail('The search could not be completed. Please try again.'); }
  }
  var timer = null;
  input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { if (input.value.trim().length >= 2) scan(false); }, 550); });
  document.getElementById('areaForm').addEventListener('submit', function (e) { e.preventDefault(); scan(false); });
  var lastClick = null;
  radiusSel.addEventListener('change', function () { scan(true, input.value.trim() ? null : lastClick); });
  map.on('click', function (e) { var t = e.originalEvent && e.originalEvent.target; if (t && t.closest && t.closest('.maplibregl-popup')) return; if (map.getLayer('lead-pins') && map.queryRenderedFeatures(e.point, { layers: ['lead-pins', 'lead-clusters'] }).length) return; lastClick = { lat: e.lngLat.lat, lng: e.lngLat.lng }; centre = [e.lngLat.lng, e.lngLat.lat]; setRing(); input.value = ''; scan(true, lastClick); });
  scan(true);
  map.once('load', function () { fitRing(); });
  /* robustness: re-measure once the page has fully laid out, and re-fit if the first view came out too far out */
  window.addEventListener('load', function () { map.resize(); });
  if (window.ResizeObserver) new ResizeObserver(function () { map.resize(); }).observe(document.getElementById('map'));
  map.once('idle', function () { if (map.getZoom() < 7) fitRing(); });
})();
</script>
</body>
</html>
"""


def render(data: dict) -> str:
    asset = _e(data.get("asset_version") or "1") + "m2"   # map rebuild: bust cached CSS/JS
    head = _HEAD.replace("__ASSET__", asset)
    script = _SCRIPT.replace("__ASSET__", asset)
    parts = [
        head, SYMBOLS, _header_html(data.get("auth")),
        HERO, _leads_section(data.get("feed")), BENEFITS,
        _activity_html(data.get("counts") or {}, data.get("total")),
        HOW, SOURCE_INFO, _offer_html(data.get("offer")),
        LOCAL, MAP_SECTION, PRICE, ADVANTAGE, TOOLS_AND_FAQ,
        _footer_html(), script,
    ]
    return "\n".join(parts)
