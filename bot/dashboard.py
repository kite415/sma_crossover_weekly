"""
Crash-recovery experiment scoreboard: renders a self-contained HTML page
(plain tables, inline CSS, no external assets) from the experiment tables.
Regenerated after every scan and served on the LAN by bot/main.py.

Forward returns are marked-to-market against each ticker's latest scanned
daily close (ticker_state) -- no network access here.
"""

import html
from datetime import datetime
from statistics import median

from bot import db
from bot.experiments import ENTRY_RULES, WINDOWS

_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem;
       background: #fafafa; color: #1a1a1a; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
table { border-collapse: collapse; margin-top: .6rem; }
th, td { border: 1px solid #ddd; padding: .35rem .7rem; font-size: .9rem;
         text-align: right; }
th { background: #efefef; } td.l, th.l { text-align: left; }
.pos { color: #0a7d33; } .neg { color: #b3261e; }
.muted { color: #777; font-size: .8rem; }
"""


def _ret_cell(ret):
    if ret is None:
        return "<td class='muted'>—</td>"
    cls = "pos" if ret >= 0 else "neg"
    return f"<td class='{cls}'>{ret:+.1f}%</td>"


def _returns(signals, prices):
    out = []
    for s in signals:
        cur = prices.get(s["ticker"])
        if cur and s["price"]:
            out.append((cur - s["price"]) / s["price"] * 100.0)
    return out


def render(conn):
    signals = db.get_experiment_signals(conn)
    prices = db.latest_closes(conn)
    states = db.get_all_experiment_states(conn)

    parts = [
        f"<style>{_CSS}</style>",
        "<h1>Crash-recovery experiments</h1>",
        f"<p class='muted'>Forward-testing scoreboard · marked to the last "
        f"scan · generated {datetime.now():%Y-%m-%d %H:%M}</p>",
    ]

    # ---- summary: one row per window x rule (+ the live baseline) ----
    parts.append("<h2>Scoreboard</h2>")
    parts.append(
        "<table><tr><th class='l'>Crash window</th><th class='l'>Entry rule</th>"
        "<th>Signals</th><th>Win rate</th><th>Avg</th><th>Median</th></tr>"
    )
    combos = [(w, r) for w in WINDOWS for r in ENTRY_RULES] + [("live", "buy")]
    for window, rule in combos:
        rows = [s for s in signals if s["window"] == window and s["rule"] == rule]
        rets = _returns(rows, prices)
        if not rows:
            continue
        win = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else None
        parts.append(
            f"<tr><td class='l'>{html.escape(window)}</td>"
            f"<td class='l'>{html.escape(rule)}</td><td>{len(rows)}</td>"
            + (f"<td>{win:.0f}%</td>" if win is not None else "<td class='muted'>—</td>")
            + _ret_cell(sum(rets) / len(rets) if rets else None)
            + _ret_cell(median(rets) if rets else None)
            + "</tr>"
        )
    parts.append("</table>")
    if not signals:
        parts.append("<p class='muted'>No entry signals yet — cases below are "
                     "being watched; rows appear as recoveries begin.</p>")

    # ---- open cases ----
    open_cases = sorted(
        ((t, c) for t, c in states.items() if c), key=lambda tc: tc[1]["opened"]
    )
    parts.append(f"<h2>Watching ({len(open_cases)} open cases)</h2>")
    parts.append(
        "<table><tr><th class='l'>Ticker</th><th>Window</th><th>Opened</th>"
        "<th>Trough</th><th>Last</th><th>Off trough</th>"
        "<th class='l'>Rules pending</th></tr>"
    )
    for ticker, case in open_cases:
        cur = prices.get(ticker)
        off = (
            (cur - case["trough"]) / case["trough"] * 100.0
            if cur and case.get("trough") else None
        )
        pending = [r for r in ENTRY_RULES if r not in (case.get("fired") or {})]
        parts.append(
            f"<tr><td class='l'>{html.escape(ticker)}</td>"
            f"<td>{html.escape(case['window'])}</td><td>{case['opened']}</td>"
            f"<td>{case['trough']:.2f}</td>"
            + (f"<td>{cur:.2f}</td>" if cur else "<td class='muted'>—</td>")
            + _ret_cell(off)
            + f"<td class='l muted'>{html.escape(', '.join(pending))}</td></tr>"
        )
    parts.append("</table>")

    # ---- recent signals ----
    parts.append("<h2>Signals (most recent first)</h2>")
    parts.append(
        "<table><tr><th>Date</th><th class='l'>Ticker</th><th>Window</th>"
        "<th class='l'>Rule</th><th>Entry</th><th>Last</th><th>Return</th></tr>"
    )
    for s in list(reversed(signals))[:300]:
        cur = prices.get(s["ticker"])
        ret = (cur - s["price"]) / s["price"] * 100.0 if cur and s["price"] else None
        parts.append(
            f"<tr><td>{s['signal_date']}</td>"
            f"<td class='l'>{html.escape(s['ticker'])}</td>"
            f"<td>{html.escape(s['window'])}</td>"
            f"<td class='l'>{html.escape(s['rule'])}</td>"
            f"<td>{s['price']:.2f}</td>"
            + (f"<td>{cur:.2f}</td>" if cur else "<td class='muted'>—</td>")
            + _ret_cell(ret)
            + "</tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


def write(conn, path="data/dashboard.html"):
    content = render(conn)
    with open(path, "w") as fh:
        fh.write(content)
    return path
