from pathlib import Path

from config import UI_DIR

_DISCLAIMER_HTML = """
<div id="gc-disclaimer-banner" role="note" style="position:fixed;bottom:0;left:0;right:0;z-index:2147483647;background:#2a1d00;color:#ffd280;border-top:1px solid #6a4a00;padding:8px 14px;font-family:ui-sans-serif,system-ui,sans-serif;font-size:12px;line-height:1.4;display:flex;gap:12px;align-items:center;justify-content:center;box-shadow:0 -2px 8px rgba(0,0,0,.45);">
  <span style="opacity:.95;">
    <strong style="color:#ffc34d;">Not financial advice.</strong>
    GeoClaw is an educational research tool. Signals, theses, and backtests are informational only —
    they are not buy/sell recommendations. Do your own research and consult a licensed advisor before trading.
  </span>
  <button type="button" aria-label="Dismiss disclaimer" onclick="var e=document.getElementById('gc-disclaimer-banner');if(e){e.remove();try{sessionStorage.setItem('gc_disclaimer_hidden','1');}catch(_){}};" style="background:transparent;border:1px solid #6a4a00;color:#ffd280;cursor:pointer;padding:2px 8px;border-radius:4px;font-size:11px;">Dismiss</button>
  <script>(function(){try{if(sessionStorage.getItem('gc_disclaimer_hidden')==='1'){var e=document.getElementById('gc-disclaimer-banner');if(e)e.remove();}}catch(_){}})();</script>
</div>
"""


def _inject_disclaimer(html: str) -> str:
    if "gc-disclaimer-banner" in html:
        return html
    lower = html.lower()
    idx = lower.rfind("</body>")
    if idx == -1:
        return html + _DISCLAIMER_HTML
    return html[:idx] + _DISCLAIMER_HTML + html[idx:]


def _read_text(name: str) -> str:
    path = UI_DIR / name
    return path.read_text(encoding="utf-8")


def render_terminal_page() -> str:
    return _inject_disclaimer(_read_text("terminal.html"))


def render_terminal_asset(name: str) -> str:
    html = _read_text(name)
    if name.lower().endswith(".html"):
        return _inject_disclaimer(html)
    return html


def terminal_asset_path(name: str) -> Path:
    return UI_DIR / name
