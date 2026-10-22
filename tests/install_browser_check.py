#!/usr/bin/env python3
"""
tests/install_browser_check.py -- real-browser check of the app installation experience (home page header + /get-app).
Needs playwright + Chromium. Not part of `unittest discover`. Exit 0 = all passed.
Pretends to be a desktop, an Android phone (with the browser's install event), an iPhone, and an already-installed app.
"""
import ast, os, sys
from playwright.sync_api import sync_playwright

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, APP)
import home_v2  # noqa: E402

def get_app_html():
    tree = ast.parse(open(os.path.join(APP, "main.py"), encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_GET_APP_HTML":
            return ast.literal_eval(n.value)
    raise SystemExit("_GET_APP_HTML not found")

DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
BEFORE = "(()=>{const e=new Event('beforeinstallprompt',{cancelable:true});e.prompt=()=>{window.__prompted=(window.__prompted||0)+1;return Promise.resolve()};e.userChoice=Promise.resolve({outcome:'accepted'});window.__bipDefaultPrevented=!window.dispatchEvent(e);})()"
STANDALONE = "(()=>{const o=window.matchMedia.bind(window);window.matchMedia=q=>q.includes('display-mode: standalone')?{matches:true,media:q,addEventListener(){},removeEventListener(){}}:o(q);})()"
STUB_MAP = "window.maplibregl=undefined;"

results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

def page_for(p, ua, html, mobile, standalone=False):
    b = p.chromium.launch()
    ctx = b.new_context(user_agent=ua, viewport={"width": 390 if mobile else 1280, "height": 800}, is_mobile=mobile, has_touch=mobile)
    if standalone: ctx.add_init_script(STANDALONE)
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)) if "ServiceWorker" not in str(e) else None)  # SW cannot load through the test router
    def route(r):
        u = r.request.url
        if u.endswith("/page"): return r.fulfill(body=html, content_type="text/html")
        if u.endswith("/sw.js"): return r.fulfill(body="self.addEventListener('fetch',()=>{});", content_type="application/javascript")
        if "/static/" in u:
            f = os.path.join(APP, "static", u.split("/static/", 1)[1].split("?")[0])
            if os.path.exists(f): return r.fulfill(path=f)
        return r.fulfill(status=204, body="")
    pg.route("**/*", route)
    pg.goto("https://treekey.test/page"); pg.wait_for_timeout(600)
    return b, pg, errs

home = home_v2.render({})
getapp = get_app_html()
visible = lambda pg, sel: pg.evaluate("s=>{const e=document.querySelector(s);return !!e && getComputedStyle(e).display!=='none' && !e.classList.contains('tk-hide')}", sel)

with sync_playwright() as p:
    # ---- desktop home page
    b, pg, errs = page_for(p, DESKTOP, home, False)
    check("desktop: button says 'Get TreeKey on your phone'", pg.inner_text("#pwaInstallBtn").strip() == "Get TreeKey on your phone", pg.inner_text("#pwaInstallBtn"))
    pg.evaluate(BEFORE); pg.wait_for_timeout(100)
    check("desktop: browser install event is suppressed and never prompted", pg.evaluate("window.__bipDefaultPrevented===true && !window.__prompted"))
    pg.click("#pwaInstallBtn"); pg.wait_for_timeout(150)
    check("desktop: click shows QR code and still never prompts", visible(pg, "#pwaInstallTip img[src='/static/images/get-app-qr.png']") and not pg.evaluate("window.__prompted"))
    check("desktop: no Apple steps shown", "Add to Home Screen" not in pg.inner_text("#pwaInstallTip"))
    check("desktop: no script errors", not errs, str(errs)); b.close()

    # ---- Android home page
    b, pg, errs = page_for(p, ANDROID, home, True)
    check("android: button hidden until the browser offers install", not visible(pg, "#pwaInstallBtn"))
    pg.evaluate(BEFORE); pg.wait_for_timeout(100)
    check("android: button is kept out of the phone header until the menu is opened (approved design)", not visible(pg, "#pwaInstallBtn"))
    pg.click(".mobile-toggle"); pg.wait_for_timeout(150)
    check("android: button appears in the menu once the browser offers install", visible(pg, "#pwaInstallBtn"))
    check("android: label is 'Install app'", pg.inner_text("#pwaInstallBtn").strip() == "Install app" or pg.evaluate("document.getElementById('pwaInstallBtn').textContent.trim()") == "Install app")
    pg.evaluate("document.getElementById('pwaInstallBtn').click()"); pg.wait_for_timeout(150)
    check("android: tap calls the native install prompt once", pg.evaluate("window.__prompted")==1)
    check("android: no Apple instructions shown", not visible(pg, "#pwaInstallTip"))
    check("android: no script errors", not errs, str(errs)); b.close()

    # ---- iPhone home page
    b, pg, errs = page_for(p, IPHONE, home, True)
    check("iphone: install button shown", pg.evaluate("!document.getElementById('pwaInstallBtn').classList.contains('tk-hide')"))
    pg.evaluate("document.getElementById('pwaInstallBtn').click()"); pg.wait_for_timeout(150)
    txt = pg.evaluate("document.getElementById('pwaInstallTip').innerText")
    check("iphone: steps are Share, Add to Home Screen, Add", "Share" in txt and "Add to Home Screen" in txt and "Add”" in txt, txt)
    check("iphone: 'two quick taps' is gone", "two quick taps" not in home.lower() and "quick taps" not in txt.lower())
    check("iphone: no script errors", not errs, str(errs)); b.close()

    # ---- installed app (every device type): nothing promoted
    for name, ua, mob in (("desktop", DESKTOP, False), ("android", ANDROID, True), ("iphone", IPHONE, True)):
        b, pg, errs = page_for(p, ua, home, mob, standalone=True)
        pg.evaluate(BEFORE); pg.wait_for_timeout(2300)
        check(f"installed ({name}): install button and tip stay hidden", not visible(pg, "#pwaInstallBtn") and not visible(pg, "#pwaInstallTip"))
        b.close()

    # ---- /get-app page
    b, pg, errs = page_for(p, DESKTOP, getapp, False)
    check("get-app (desktop): shows QR, not install steps", visible(pg, "#s-other") and not visible(pg, "#s-ios") and not visible(pg, "#s-android")); b.close()
    b, pg, errs = page_for(p, IPHONE, getapp, True)
    t = pg.inner_text("#s-ios")
    check("get-app (iphone): shows the three Apple steps only", visible(pg, "#s-ios") and not visible(pg, "#s-android") and "Share" in t and "Add to Home Screen" in t and "Add" in t); b.close()
    b, pg, errs = page_for(p, ANDROID, getapp, True)
    check("get-app (android): no Apple steps visible", visible(pg, "#s-android") and not visible(pg, "#s-ios"))
    pg.evaluate(BEFORE); pg.wait_for_timeout(100)
    check("get-app (android): install button after browser offer", visible(pg, "#installBtn"))
    pg.click("#installBtn"); pg.wait_for_timeout(150)
    check("get-app (android): tap calls the native prompt", pg.evaluate("window.__prompted")==1); b.close()
    b, pg, errs = page_for(p, ANDROID, getapp, True)
    pg.wait_for_timeout(2800)
    check("get-app (android, no browser offer): menu fallback shown", visible(pg, "#androidFallback")); b.close()
    b, pg, errs = page_for(p, ANDROID, getapp, True, standalone=True)
    check("get-app (installed): says already installed", visible(pg, "#s-installed") and not visible(pg, "#s-android")); b.close()

print(f"\n{sum(results)}/{len(results)} checks passed.")
sys.exit(0 if all(results) else 1)
