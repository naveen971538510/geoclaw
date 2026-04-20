from pathlib import Path

from config import UI_DIR

_AUTH_SHIM_HTML = """
<script id="gc-auth-shim">
(function(){
  var KEY='gc_access_token';
  // Pages where a 401 should NOT bounce the user to /login — they are
  // part of the unauthenticated auth flow itself. Bouncing mid-reset
  // would destroy the token the user just clicked in their email.
  var PUBLIC_PATHS={'/login':1,'/forgot-password':1,'/reset-password':1,'/verify-email':1};
  // Public /api/* endpoints that should NOT get an Authorization header
  // attached (and should not trigger logout-on-401).
  var PUBLIC_API={'/api/auth/login':1,'/api/auth/signup':1,'/api/auth/verify-email':1,
                  '/api/auth/request-password-reset':1,'/api/auth/reset-password':1};
  // EventSource needs the token in the query string (browsers can't send
  // custom headers on SSE). Scope this to SSE endpoints only — attaching
  // tokens to every /api/* URL leaks JWTs into server / proxy access logs.
  var SSE_PATHS=[/^\\/api\\/stream(\\/|$)/];
  function tok(){try{return localStorage.getItem(KEY)||'';}catch(_){return '';}}
  function isProtectedApi(urlStr){
    try{
      var u=new URL(urlStr,window.location.origin);
      if(u.origin!==window.location.origin) return false;
      if(!u.pathname.startsWith('/api/')) return false;
      if(PUBLIC_API[u.pathname]) return false;
      return true;
    }catch(_){return false;}
  }
  function isSseUrl(u){
    for(var i=0;i<SSE_PATHS.length;i++){ if(SSE_PATHS[i].test(u.pathname)) return true; }
    return false;
  }
  var _fetch=window.fetch;
  window.fetch=function(input,init){
    var urlStr=typeof input==='string'?input:(input&&input.url)||'';
    try{
      if(isProtectedApi(urlStr)){
        var t=tok();
        if(t){
          init=init||{};
          var h=new Headers((init&&init.headers)||(input&&input.headers)||{});
          if(!h.has('Authorization')) h.set('Authorization','Bearer '+t);
          init.headers=h;
        }
      }
    }catch(_){}
    return _fetch.call(this,input,init).then(function(resp){
      try{
        if(resp&&resp.status===401&&isProtectedApi(resp.url||urlStr)){
          try{localStorage.removeItem(KEY);localStorage.removeItem('gc_user');}catch(_){}
          if(!PUBLIC_PATHS[window.location.pathname]) window.location.href='/login';
        }
      }catch(_){}
      return resp;
    });
  };
  if(typeof window.EventSource==='function'){
    var _ES=window.EventSource;
    function W(url,cfg){
      try{
        var u=new URL(url,window.location.origin);
        if(u.origin===window.location.origin&&isSseUrl(u)){
          var t=tok();
          if(t&&!u.searchParams.has('token')){u.searchParams.set('token',t);url=u.toString();}
        }
      }catch(_){}
      return new _ES(url,cfg);
    }
    W.prototype=_ES.prototype;
    W.CONNECTING=_ES.CONNECTING;W.OPEN=_ES.OPEN;W.CLOSED=_ES.CLOSED;
    window.EventSource=W;
  }
  window.gcAuth={token:tok,logout:function(){try{localStorage.removeItem(KEY);localStorage.removeItem('gc_user');}catch(_){}window.location.href='/login';}};
})();
</script>
"""

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


def _inject_auth_shim(html: str) -> str:
    """Inject the JWT auth shim into <head> (or early <body>) so it wraps
    window.fetch and EventSource before any page script fires."""
    if 'id="gc-auth-shim"' in html:
        return html
    lower = html.lower()
    idx = lower.find("</head>")
    if idx != -1:
        return html[:idx] + _AUTH_SHIM_HTML + html[idx:]
    idx = lower.find("<body")
    if idx != -1:
        end = html.find(">", idx)
        if end != -1:
            insert_at = end + 1
            return html[:insert_at] + _AUTH_SHIM_HTML + html[insert_at:]
    return _AUTH_SHIM_HTML + html


def _inject_disclaimer(html: str) -> str:
    html = _inject_auth_shim(html)
    if 'id="gc-disclaimer-banner"' in html:
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
