"""TEN Capital Network look for the Streamlit app.

Dark navy surfaces, Sora / Inter / JetBrains Mono type, and the logo's
coral → amber → teal accents. All user-supplied text passed to these helpers
is HTML-escaped.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "favicon.png"


@lru_cache(maxsize=1)
def logo_html() -> str:
    """The TEN Capital Network mark, embedded as a data URI so it renders without static serving."""
    try:
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f'<img class="tc-mark" src="data:image/png;base64,{data}" alt="TEN Capital Network logo">'

FILE_ICON = """
<svg viewBox="0 0 24 24" fill="none" stroke="#F3F6FA" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
  <path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"/>
</svg>
"""

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
:root{
  --navy-950:#0B1526; --navy-900:#101E33; --navy-800:#16283F; --navy-700:#1E354F;
  --coral:#EE5A4E; --coral-soft:#F0776C; --amber:#F3A22A; --teal:#35BEBB;
  --ink-100:#F3F6FA; --ink-300:#C4D0E0; --ink-500:#7E90A8; --ink-600:#5C6E86;
}
html, body, .stApp, [data-testid="stMarkdownContainer"], input, textarea, button, label, p, li{
  font-family:'Inter', sans-serif;
}
.stApp{
  background:
    radial-gradient(480px 380px at 14% 8%, rgba(238,90,78,0.13), transparent 60%),
    radial-gradient(480px 380px at 86% 6%, rgba(243,162,42,0.10), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.10), transparent 60%),
    var(--navy-950);
  background-attachment: fixed;
}
[data-testid="stHeader"]{ background: transparent; }
[data-testid="stSidebar"]{ background: var(--navy-900); border-right:1px solid var(--navy-700); }
h1, h2, h3, h4{ font-family:'Sora', sans-serif !important; letter-spacing:-0.01em; }
code{ font-family:'JetBrains Mono', monospace !important; }

/* brand lockup */
.tc-brand{ display:flex; align-items:center; gap:12px; max-width:680px; margin:4px auto 22px; padding-left:4px; }
.tc-mark{ width:40px; height:40px; flex-shrink:0; border-radius:50%; background:#FFFFFF; padding:2px;
  box-shadow:0 0 0 1px var(--navy-700); }
.tc-word{ font-family:'Sora',sans-serif; font-weight:800; font-size:15px; letter-spacing:.04em; line-height:1.15;
  color:var(--ink-100); text-transform:uppercase; }
.tc-word span{ display:block; font-weight:600; font-size:10px; letter-spacing:.22em; color:var(--ink-500); margin-top:2px; }

/* hero card (keyed container) */
.st-key-tc-hero{
  max-width: 680px; margin: 0 auto;
  background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-800) 100%);
  border:1px solid var(--navy-700); border-radius:20px; padding:44px 44px 30px !important;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
  position:relative; overflow:hidden;
}
.st-key-tc-hero::after{
  content:""; position:absolute; top:0; left:44px; right:44px; height:2px; border-radius:2px;
  background: linear-gradient(90deg, var(--coral), var(--amber), var(--teal));
}
.tc-eyebrow{ display:flex; align-items:center; gap:8px; font-family:'JetBrains Mono',monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--teal); margin-bottom:14px; }
.tc-eyebrow::before{ content:""; width:6px; height:6px; border-radius:50%; background:var(--teal);
  box-shadow:0 0 0 3px rgba(53,190,187,.18); }
.tc-h1{ font-family:'Sora',sans-serif; font-size:28px; font-weight:700; line-height:1.25; margin:0 0 12px;
  letter-spacing:-0.01em; color:var(--ink-100); }
.tc-h1 .arrow{ color:var(--ink-500); font-weight:400; margin:0 6px; }
.tc-h1 .to{ background:linear-gradient(90deg, var(--coral-soft), var(--amber)); -webkit-background-clip:text;
  background-clip:text; color:transparent; }
.tc-lede{ color:var(--ink-300); font-size:15px; line-height:1.6; margin:0 0 22px; max-width:50ch; }
.tc-disclosure{ margin-top:18px; padding-top:16px; border-top:1px solid var(--navy-700); font-size:12px;
  line-height:1.6; color:var(--ink-500); }
.tc-disclosure code, .tc-chip{ font-family:'JetBrains Mono',monospace; background:var(--navy-950);
  border:1px solid var(--navy-700); color:var(--ink-300); padding:2px 6px; border-radius:5px; font-size:11.5px; }
.tc-footer{ text-align:center; margin:22px 0 8px; font-family:'JetBrains Mono',monospace; font-size:11px;
  letter-spacing:.08em; color:var(--ink-600); text-transform:uppercase; }

/* dropzone uploader */
.st-key-tc-hero [data-testid="stFileUploader"] label{ display:none; }
[data-testid="stFileUploaderDropzone"]{
  border:1.5px dashed var(--navy-700) !important; border-radius:14px !important;
  background: rgba(255,255,255,0.015) !important; padding:30px 24px !important;
  transition: border-color .18s ease, background .18s ease;
}
[data-testid="stFileUploaderDropzone"]:hover{ border-color:var(--teal) !important; background:rgba(53,190,187,.05) !important; }
[data-testid="stFileUploaderDropzoneInstructions"] small,
[data-testid="stFileUploaderDropzoneInstructions"] span{ font-family:'JetBrains Mono',monospace; color:var(--ink-500); }
[data-testid="stFileUploaderDropzone"] button{
  border:1px solid var(--navy-700) !important; background:var(--navy-950) !important; color:var(--ink-100) !important;
  border-radius:10px !important;
}

/* primary CTA */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stDownloadButton"] button[kind="primary"]{
  background: linear-gradient(90deg, var(--coral) 0%, var(--coral-soft) 45%, var(--amber) 100%) !important;
  color:#17130E !important; border:none !important; border-radius:12px !important;
  font-family:'Sora',sans-serif !important; font-weight:700 !important; letter-spacing:.01em;
  box-shadow: 0 10px 24px -10px rgba(238,90,78,.45); transition: filter .15s ease, transform .15s ease;
}
[data-testid="stBaseButton-primary"] p{ font-family:'Sora',sans-serif !important; font-weight:700 !important; color:#17130E !important; }
[data-testid="stBaseButton-primary"]:hover{ filter:brightness(1.06); transform:translateY(-1px); }
[data-testid="stBaseButton-primary"]:disabled{ filter:grayscale(.7) brightness(.6); box-shadow:none; transform:none; }
[data-testid="stBaseButton-secondary"]{ border-radius:10px !important; border-color:var(--navy-700) !important; }

/* step headers */
.tc-step{ display:flex; align-items:baseline; gap:14px; margin:36px 0 12px; }
.tc-step .n{ font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.14em; color:var(--teal); }
.tc-step .t{ font-family:'Sora',sans-serif; font-size:21px; font-weight:700; color:var(--ink-100); }
.tc-step::after{ content:""; flex:1; height:1px; background:linear-gradient(90deg, var(--navy-700), transparent); }

/* surfaces */
[data-testid="stVerticalBlockBorderWrapper"]{ border-color:var(--navy-700) !important; border-radius:14px !important; }
[data-testid="stMetric"]{ background:var(--navy-900); border:1px solid var(--navy-700); border-radius:12px; padding:12px 16px; }
[data-testid="stMetricValue"]{ font-family:'Sora',sans-serif; }
[data-testid="stMetricLabel"] p{ font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--ink-500); }
[data-testid="stExpander"] details{ border-color:var(--navy-700) !important; border-radius:12px !important; background:rgba(255,255,255,.01); }
[data-testid="stTabs"] button[role="tab"] p{ font-family:'Sora',sans-serif; font-weight:600; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{ background: linear-gradient(90deg, var(--coral), var(--amber)); }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea{ background:var(--navy-950) !important; }
[data-testid="stImage"] img{ border-radius:8px; border:1px solid var(--navy-700); }

@media (max-width: 480px){
  .st-key-tc-hero{ padding:32px 22px 24px !important; }
  .tc-h1{ font-size:23px; }
}
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def brand_html() -> str:
    return f'<div class="tc-brand">{logo_html()}<div class="tc-word">Ten Capital<span>Network</span></div></div>'


def brand() -> None:
    st.markdown(brand_html(), unsafe_allow_html=True)


def hero_header(eyebrow: str, title: str, highlight: str, lede: str) -> None:
    st.markdown(
        f'<div class="tc-eyebrow">{escape(eyebrow)}</div>'
        + f'<div class="tc-h1">{escape(title)}<span class="arrow">&rarr;</span><span class="to">{escape(highlight)}</span></div>'
        + f'<p class="tc-lede">{escape(lede)}</p>',
        unsafe_allow_html=True,
    )


def disclosure(parts: list[str], emails: list[str] | None = None) -> None:
    text = " ".join(escape(p) for p in parts)
    if emails:
        text += " A copy of every generated review is emailed to " + ", ".join(
            f"<code>{escape(e)}</code>" for e in emails) + "."
    st.markdown(f'<div class="tc-disclosure">{text}</div>', unsafe_allow_html=True)


def step(number: int, title: str) -> None:
    st.markdown(
        f'<div class="tc-step"><span class="n">{number:02d}</span><span class="t">{escape(title)}</span></div>',
        unsafe_allow_html=True,
    )


def footer() -> None:
    st.markdown('<div class="tc-footer">Powered by TEN Capital Network</div>', unsafe_allow_html=True)
