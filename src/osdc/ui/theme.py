"""Visual language. One file, so the look stays coherent.

Minimal: near-black canvas, one accent, generous whitespace, no chrome. Everything that
isn't content gets out of the way.
"""

from __future__ import annotations

from nicegui import ui

ACCENT = "#6366f1"

CSS = """
:root {
  --bg:        #0b0b0f;
  --surface:   #141419;
  --surface-2: #1b1b22;
  --border:    #26262f;
  --text:      #e8e8ed;
  --muted:     #8b8b99;
  --accent:    #6366f1;
}

body, .nicegui-content { background: var(--bg) !important; color: var(--text); }
.q-page, .q-layout { background: var(--bg) !important; }

/* Kill Quasar's default card chrome — shadows read as clutter at this density. */
.q-card { background: var(--surface) !important; box-shadow: none !important;
          border: 1px solid var(--border); border-radius: 14px; }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2a2a33; border-radius: 6px;
                            border: 3px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: #3a3a45; }

/* --- side rail --------------------------------------------------------- */
.rail { background: var(--bg); border-right: 1px solid var(--border); }
.rail-item { color: var(--muted); border-radius: 10px; transition: all .12s ease; }
.rail-item:hover { background: var(--surface-2); color: var(--text); }
.rail-item.active { background: var(--surface-2); color: var(--text); }

/* --- chat -------------------------------------------------------------- */
.msg-user { background: var(--surface-2); border: 1px solid var(--border);
            border-radius: 16px 16px 4px 16px; padding: 12px 16px; max-width: 80%;
            margin-left: auto; }
.msg-bot  { padding: 4px 2px; max-width: 100%; line-height: 1.7; }
.msg-bot p { margin: 0 0 .7em 0; }
.msg-bot code { background: var(--surface-2); padding: 1px 5px; border-radius: 4px;
                font-size: .9em; }

.composer { background: var(--surface); border: 1px solid var(--border);
            border-radius: 16px; transition: border-color .15s ease; }
.composer:focus-within { border-color: var(--accent); }
.composer .q-field__control { height: auto !important; padding: 0 !important; }
.composer .q-field__control:before,
.composer .q-field__control:after { display: none !important; }

/* --- sources ----------------------------------------------------------- */
.src { background: var(--surface-2); border: 1px solid var(--border);
       border-radius: 10px; transition: border-color .12s ease; }
.src:hover { border-color: var(--accent); }

/* --- suggestion chips --------------------------------------------------- */
.chip { background: var(--surface); border: 1px solid var(--border);
        border-radius: 12px; padding: 14px 16px; cursor: pointer;
        transition: all .12s ease; }
.chip:hover { border-color: var(--accent); background: var(--surface-2); }

/* --- image grid --------------------------------------------------------- */
.tile { border-radius: 12px; overflow: hidden; border: 1px solid var(--border);
        background: var(--surface); position: relative; }
.tile img { width: 100%; height: 160px; object-fit: cover; display: block; }
.tile .score { position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,.72);
               color: #fff; font-size: 11px; padding: 2px 7px; border-radius: 6px; }

/* --- plan preview -------------------------------------------------------- */
.plan-folder { color: var(--accent); font-weight: 600; font-size: 13px;
               letter-spacing: .01em; }
.plan-file { border-left: 2px solid var(--border); }
.plan-file:hover { border-left-color: var(--accent); }

.dim { color: var(--muted); }
.mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }
"""


def apply() -> None:
    ui.add_head_html(f"<style>{CSS}</style>")
    ui.colors(primary=ACCENT, dark="#0b0b0f")
