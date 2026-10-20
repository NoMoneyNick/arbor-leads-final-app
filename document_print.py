"""
document_print.py -- shared look for the printable one-page documents (street flyer, storm quote).

The letter has its own print CSS inside letter_content.py (it needs exact address/barcode clearances).
These two documents share this stylesheet and banner so they stay in step.

TO CHANGE THE LOOK: colours and fonts come from brand.py; layout lives in CSS below. The pages that use
it are main.py -> generate_street_flyer and generate_storm_quote. Screen-only controls (back link, print
button) are hidden when printing.
"""
import brand

C = brand.COLORS


def css() -> str:
    return f"""
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background: {C['navy']}; color: {C['ink']}; font: 15px/1.65 Arial, Helvetica, sans-serif; }}
        .tk-doc-controls {{ max-width: 740px; margin: 22px auto 0 auto; padding: 0 22px; display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }}
        .btn-back {{ color: {C['gold']}; font-size: 13px; text-decoration: none; font-weight: bold; }}
        .btn-print {{ background: {C['gold']}; color: #142b20; border: 1px solid {C['gold_line']}; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; }}
        .tk-doc {{ max-width: 740px; margin: 20px auto 40px auto; background: {C['ivory']}; padding: 38px 45px; box-shadow: 0 14px 40px rgba(0,0,0,.27); }}
        .tk-doc-brand {{ background: linear-gradient(120deg, {C['print_banner_from']}, {C['print_banner_to']}); color: {C['ivory_text']}; display: flex; align-items: center; gap: 16px; padding: 18px 24px; border: 1px solid {C['gold_line']}; border-radius: 5px; margin-bottom: 36px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        .tk-doc-brand img {{ width: 64px; height: 64px; object-fit: contain; }}
        .tk-doc-brand strong {{ font: 34px {brand.SERIF}; font-weight: normal; }}
        .tk-doc-brand small {{ display: block; color: {C['gold']}; font-size: 9px; letter-spacing: 1.4px; }}
        .badge {{ font-size: 11px; letter-spacing: 1.2px; color: {C['print_gold_label']}; text-transform: uppercase; font-weight: bold; }}
        .tk-doc-top {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }}
        .tk-doc-creds {{ font-size: 12px; color: {C['muted']}; }}
        .tk-doc h2 {{ font: normal 32px/1.2 {brand.SERIF}; color: {C['print_heading']}; margin: 15px 0 23px 0; }}
        .tk-doc h4 {{ font: normal 21px {brand.SERIF}; color: {C['print_heading']}; margin: 25px 0 10px 0; }}
        .tk-doc p {{ margin: 16px 0; line-height: 1.7; }}
        .tk-doc ul {{ padding-left: 22px; line-height: 2; margin: 0 0 20px 0; }}
        .tk-doc-legal {{ border-left: 3px solid {C['print_gold_rule']}; padding: 14px 18px; background: #f2eee1; font-size: 13px; margin: 16px 0; }}
        .tk-doc-contact {{ background: {brand.COLORS['forest']}; background: linear-gradient(110deg, {C['print_panel_from']}, {C['print_panel_to']}); color: {C['print_ivory_on_dark']}; border: 1px solid {C['gold_line']}; border-radius: 6px; padding: 22px 24px; margin-top: 28px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        .tk-doc-contact .label {{ font-size: 10px; text-transform: uppercase; color: {C['print_gold_on_dark']}; font-weight: bold; }}
        .tk-doc-contact .value {{ font: bold 21px {brand.SERIF}; margin-top: 7px; }}
        .tk-doc-contact .right {{ text-align: right; }}
        .tk-doc-foot {{ margin-top: 44px; padding-top: 14px; border-top: 1px solid {C['gold_line']}; font-size: 10px; color: {C['muted']}; }}
        @media (max-width: 780px) {{ .tk-doc {{ margin: 20px 12px 30px 12px; padding: 26px 25px; }} .tk-doc h2 {{ font-size: 28px; }} .tk-doc-contact .right {{ text-align: left; }} }}
        @page {{ size: A4; margin: 15mm; }}
        @media print {{ body {{ background: #fff; }} .tk-doc-controls {{ display: none; }} .tk-doc {{ box-shadow: none; background: #fff; margin: 0; max-width: none; padding: 0; }} }}
    """


def banner_html() -> str:
    """Crest + wordmark banner at the top of every printable document."""
    return (
        '<header class="tk-doc-brand">'
        '<img src="/static/images/treekey-crest.png" alt="TreeKey crest" width="64" height="64">'
        f'<div><strong>{brand.NAME}</strong><small>{brand.TAGLINE}</small></div></header>'
    )


def footer_html() -> str:
    return f'<footer class="tk-doc-foot">{brand.NAME} &middot; {brand.CANONICAL_HOST}</footer>'
