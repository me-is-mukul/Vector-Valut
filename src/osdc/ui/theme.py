"""Visual language. One file, so the look stays coherent.

Minimal, but not flat: layered surfaces, one accent, generous whitespace, and enough
contrast to make the app feel intentional without getting busy.
"""

from __future__ import annotations

from nicegui import ui

# Must match --accent below: this is what Quasar paints spinners and primary buttons with,
# and two different purples on one screen reads as a bug, not a palette.
ACCENT = "#7c8cff"

CSS = """
:root {
        --bg:        #090b10;
        --bg-glow:   radial-gradient(circle at top,
                       rgba(99, 102, 241, 0.18), transparent 38%),
                     radial-gradient(circle at 80% 10%,
                       rgba(14, 165, 233, 0.10), transparent 24%);
        --surface:   #121621;
        --surface-2: #181d2a;
        --surface-3: #1d2433;
        --border:    #283044;
        --text:      #edf0f7;
        --muted:     #98a2b3;
        --accent:    #7c8cff;
}

body, .nicegui-content {
        background: var(--bg) var(--bg-glow) !important;
        color: var(--text);
}
.q-page, .q-layout { background: var(--bg) !important; }

/* Kill Quasar's default card chrome — shadows read as clutter at this density. */
.q-card { background: var(--surface) !important; box-shadow: none !important;
          border: 1px solid var(--border); border-radius: 14px; }

.q-btn { text-transform: none; letter-spacing: 0; }

.q-field--outlined .q-field__control { background: var(--surface); }
.q-field--outlined .q-field__control:hover { border-color: var(--accent); }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2a2a33; border-radius: 6px;
                            border: 3px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: #3a3a45; }

/* --- side rail --------------------------------------------------------- */
.rail {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), transparent), var(--bg);
        border-right: 1px solid var(--border);
}
.rail-item { color: var(--muted); border-radius: 12px; transition: all .16s ease; }
.rail-item:hover { background: var(--surface-2); color: var(--text); transform: translateY(-1px); }
.rail-item.active { background: var(--surface-2); color: var(--text);
                    box-shadow: inset 0 0 0 1px rgba(124, 140, 255, 0.18); }

/* --- chat -------------------------------------------------------------- */
.msg-user {
        background: linear-gradient(180deg, rgba(124, 140, 255, 0.08), transparent),
                    var(--surface-2);
        border: 1px solid var(--border); border-radius: 16px 16px 4px 16px; padding: 12px 16px;
        max-width: 80%; margin-left: auto;
}
.msg-bot  { padding: 4px 2px; max-width: 100%; line-height: 1.7; animation: fade-up .16s ease; }
.msg-bot p { margin: 0 0 .7em 0; }
.msg-bot code { background: var(--surface-2); padding: 1px 5px; border-radius: 4px;
                font-size: .9em; }

/* --- status banner ----------------------------------------------------- */
.status-banner {
        background: linear-gradient(90deg, rgba(124, 140, 255, 0.10), rgba(99, 102, 241, 0.04));
        border-bottom: 1px solid var(--border);
}

.composer {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), transparent), var(--surface);
        border: 1px solid var(--border); border-radius: 16px; transition: border-color .15s ease,
        transform .15s ease;
}
.composer:focus-within { border-color: var(--accent); transform: translateY(-1px); }
.composer .q-field__control { height: auto !important; padding: 0 !important; }
.composer .q-field__control:before,
.composer .q-field__control:after { display: none !important; }

/* --- sources ----------------------------------------------------------- */
.src { background: var(--surface-2); border: 1px solid var(--border);
        border-radius: 10px; transition: border-color .12s ease, transform .12s ease; }
.src:hover { border-color: var(--accent); transform: translateY(-1px); }

/* --- suggestion chips --------------------------------------------------- */
.chip { background: var(--surface); border: 1px solid var(--border);
        border-radius: 12px; padding: 14px 16px; cursor: pointer;
        transition: all .14s ease; }
.chip:hover { border-color: var(--accent); background: var(--surface-2);
              transform: translateY(-1px); }

/* --- image grid --------------------------------------------------------- */
.tile { border-radius: 12px; overflow: hidden; border: 1px solid var(--border);
        background: var(--surface); position: relative; transition: transform .12s ease,
        border-color .12s ease; }
.tile:hover { transform: translateY(-1px); border-color: var(--accent); }
.tile .thumb { height: 160px; background: var(--surface-2); display: flex;
               align-items: center; justify-content: center; }
.tile img { width: 100%; height: 160px; object-fit: cover; display: block; }
.tile .score { position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,.72);
               color: #fff; font-size: 11px; padding: 2px 7px; border-radius: 6px; }

/* --- plan preview -------------------------------------------------------- */
.plan-folder { color: var(--accent); font-weight: 600; font-size: 13px;
               letter-spacing: .01em; }
.plan-file { border-left: 2px solid var(--border); }
.plan-file:hover { border-left-color: var(--accent); }

.danger-zone { border-color: rgba(248, 113, 113, 0.3) !important; }

.dim { color: var(--muted); }
.mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }

@keyframes fade-up {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
}
"""


def apply() -> None:
    ui.add_head_html(f"<style>{CSS}</style>")
    ui.colors(primary=ACCENT, dark="#0b0b0f")
