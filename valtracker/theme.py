"""Light or dark, the same as every site of this host (Home Page's homepage/__init__.py).

The device's setting until the visitor picks one with the header's button (automatic -> light -> dark);
the choice is localStorage "theme", which every site of the host reads (they share one origin). The script
is inline in <head> so the page is painted in the right colours from the start; the CSP allows exactly this
script, by its hash (security.py). It also closes the language menu on a click elsewhere or Escape. Keep it the
same as Home Page's, character for character: a changed copy would need its own hash and would drift.
"""

from __future__ import annotations

import base64
import hashlib
from html import escape

from valtracker import i18n

THEME_SCRIPT = (
    '(function(){var d=document,r=d.documentElement,o=["auto","light","dark"],c={light:"#f6f4ef",dark:"#161513"};'
    'function get(){try{var t=localStorage.getItem("theme");return o.indexOf(t)>0?t:"auto"}'
    'catch(e){return r.getAttribute("data-theme")||"auto"}}'
    'function set(t){if(t==="auto")r.removeAttribute("data-theme");else r.setAttribute("data-theme",t);'
    "d.querySelectorAll('meta[name=\"theme-color\"]').forEach(function(m){"
    'm.content=c[t==="auto"?(m.media.indexOf("dark")<0?"light":"dark"):t]})}'
    "set(get());"
    'd.addEventListener("DOMContentLoaded",function(){var b=d.getElementById("theme");'
    'if(b)b.addEventListener("click",function(){var t=o[(o.indexOf(get())+1)%3];'
    'try{if(t==="auto")localStorage.removeItem("theme");else localStorage.setItem("theme",t)}catch(e){}set(t)});'
    'var l=d.querySelector("details.lang");if(l){d.addEventListener("click",function(e){if(!l.contains(e.target))l.open=false});'
    'l.addEventListener("keydown",function(e){if(e.key==="Escape"&&l.open){l.open=false;l.querySelector("summary").focus()}})}});'
    'addEventListener("storage",function(e){if(e.key==="theme"||e.key===null)set(get())})})();'
)
THEME_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(THEME_SCRIPT.encode()).digest()).decode() + "'"

_ICONS = {
    "auto": '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
    "light": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8'
    'M2.5 12H5M19 12h2.5M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/>',
    "dark": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
}
_STATES = {"auto": "自動", "light": "オフ", "dark": "オン"}


def button(lang: str) -> str:
    """The header's light/dark button: every state's icon and words are in the page and the stylesheet shows
    the current one (<html data-theme>), so it is right from the first paint."""
    spans = "".join(
        f'<span class="t-{key}"><svg viewBox="0 0 24 24" aria-hidden="true">{_ICONS[key]}</svg>'
        f'<span class="words">{escape(i18n.text(lang, "ダークモード {state}", state=i18n.text(lang, state)))}</span></span>'
        for key, state in _STATES.items()
    )
    title = escape(i18n.text(lang, "ダークモード: 自動 → オフ → オン"))
    return f'<button type="button" class="theme" id="theme" title="{title}">{spans}</button>'
