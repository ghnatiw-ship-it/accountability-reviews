#!/usr/bin/env python3
"""Weekly accountability review: one section per person in seats.json.

For each accountability on a person's Ninety seat, pull the number that measures it (if one is
connected), compare it to its goal, and write everything to one place:
  data/ledger.csv            one row per person x accountability x week (the central record)
  reports/<week>.html        the readable review for Graham

Sources: sales = Auphan POS mirror (pos_source, same as the Daily Sales Summary); labour = the
Labour 2026 sheets (labour_source, needs gws - never run inside an agent sandbox); logbook =
NocoDB manager logbooks (auphan-data-access/logbook_context).

Published (Graham's choice, 2026-11-27) with FIRST NAMES ONLY - never put a last name in seats.json.
Nothing here emails or messages anyone.

  python3 review.py                 # last completed Mon-Sun week
  python3 review.py --week 2026-11-16
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / ".claude" / "esplanade-ceo" / "agents" / "_lib"))
sys.path.insert(0, str(ROOT / "auphan-data-access"))

import eos_goals as G          # noqa: E402
import labour_source as L      # noqa: E402
import pos_source as P         # noqa: E402
import logbook_context as LB   # noqa: E402

TREND_WEEKS = 5   # the review week + 4 before it
YOY_DAYS = 364

# venue -> where each number comes from
VENUES = {
    "sy":     {"label": "Scotland Yard", "pos": "sy", "labour": ["sy"], "goal": "sy",
               "logbook": "Scotland Yard", "min_net": 100},
    "osf":    {"label": "Old Spaghetti Factory", "pos": "osf", "labour": ["osf"], "goal": "osf",
               "logbook": "Old Spaghetti Factory", "min_net": 100},
    "ecc":    {"label": "Eloise + Bar Cart", "pos": "ecc", "labour": ["eloise", "bar_cart"], "goal": "ecc",
               "logbook": "Eloise", "min_net": 100},
    "dollys": {"label": "Dolly's", "pos": "dollys", "labour": ["dollys"], "goal": "dollys",
               "logbook": "Dolly's", "min_net": 100},
    "cath":   {"label": "Bar Cathedral", "pos": "cath", "labour": ["cathedral"], "goal": "cath",
               "logbook": "Bar Cathedral", "min_net": 1000},
}

# thresholds (first pass - Graham to tune)
LABOUR_EXCEED_PTS = 1.0     # at least this far under goal = exceeding
SALES_EXCEED = 5.0          # % vs comparison
SALES_OFF = -3.0
LOGBOOK_ON = 85.0           # % of trading nights with an entry

EXCEED, ON, OFF, NODATA, INFO = "Exceeding", "On track", "Off track", "No data", "Not graded"


def days(start: dt.date, n: int = 7):
    return [start + dt.timedelta(days=i) for i in range(n)]


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {getattr(fn, '__name__', fn)}{a}: {e}", file=sys.stderr)
        return default


# ------------------------------------------------------------------ raw pulls
def pos_net(venue: str, d: dt.date):
    row = (_safe(P.day_net_meals, d, default={}) or {}).get(VENUES[venue]["pos"])
    return None if not row else row.get("net")


def labour_day(venue: str, d: dt.date):
    """(labour %, sheet sales the % is taken on, labour $) for one day, or None."""
    recs = [_safe(L.get_daily, v, d) for v in VENUES[venue]["labour"]]
    main = recs[0] or {}
    pct = main.get("labour_pct")
    sales = sum((r or {}).get("sales") or 0 for r in recs)
    if pct is None or not sales:
        return None
    dollars = main.get("labour_dollars") or pct / 100 * sales
    return pct, sales, dollars


def logbook_day(venue: str, d: dt.date, token: str):
    """(has entry, has guest-issue text) for one business day."""
    table = LB.TABLES[VENUES[venue]["logbook"]]
    rows = _safe(LB._rows_for, table, d, token, default=None)
    if rows is None:
        return None
    has = bool(LB._text(rows, LB.TEXT_FIELDS))
    issue = bool(LB._text(rows, ["Guest Issues"]))
    return has, issue


# ------------------------------------------------------------------ measures
def m_labour(venue, week):
    goal = G.GOALS.get(VENUES[venue]["goal"], {}).get("labour_pct", (None,))[0]
    trend, daily = [], []
    for w in range(TREND_WEEKS):
        start = week - dt.timedelta(days=7 * w)
        lab = base = 0.0
        for d in days(start):
            r = labour_day(venue, d)
            if not r:
                continue
            lab += r[2]; base += r[1]
            daily.append((d, r[0], r[1]))
        trend.append((start, round(lab / base * 100, 1) if base else None))
    pct = trend[0][1]
    if pct is None or goal is None:
        return {"status": NODATA, "value": "labour sheet not filled in", "target": f"≤{goal}%" if goal else ""}
    status = EXCEED if pct <= goal - LABOUR_EXCEED_PTS else ON if pct <= goal else OFF
    wk = [(d, p, s) for d, p, s in daily if week <= d < week + dt.timedelta(days=7)]
    over = sorted([x for x in wk if x[1] > goal], key=lambda x: -x[1])
    coach = []
    if over:
        coach.append("Days over goal this week: " + "; ".join(
            f"{d:%a %b %-d} {p:.1f}% on ${s:,.0f}" for d, p, s in over))
    # weekday pattern across the trend window
    by_wd = {}
    for d, p, s in daily:
        by_wd.setdefault(d.weekday(), []).append(p)
    wd = sorted(((sum(v) / len(v), k, len(v)) for k, v in by_wd.items() if len(v) >= 3), reverse=True)
    worst = [f"{dt.date(2026, 6, 1 + k):%A}s average {a:.1f}%" for a, k, n in wd[:2] if a > goal]
    if worst:
        coach.append(f"Over the last {TREND_WEEKS} weeks: " + ", ".join(worst) + f" (goal {goal}%).")
    return {"status": status, "value": f"{pct:.1f}%", "target": f"≤{goal}%",
            "trend": [(s, f"{p:.1f}%" if p is not None else "n/a") for s, p in trend], "coach": coach}


def m_sales(venue, week):
    mn = VENUES[venue]["min_net"]
    trend, rows = [], []
    for w in range(TREND_WEEKS):
        start = week - dt.timedelta(days=7 * w)
        net = ly = 0.0
        got = False
        for d in days(start):
            n = pos_net(venue, d)
            l = pos_net(venue, d - dt.timedelta(days=YOY_DAYS))
            if n is not None:
                got = True
            net += n or 0; ly += l or 0
            if w == 0:
                rows.append((d, n or 0, l or 0))
        trend.append((start, net if got else None, ly))
    net, ly = trend[0][1], trend[0][2]
    if net is None:
        return {"status": NODATA, "value": "no POS sales found", "target": ""}
    prior = [t[1] for t in trend[1:] if t[1]]
    avg4 = sum(prior) / len(prior) if prior else None
    if ly and ly > mn * 3:
        chg, basis = (net - ly) / ly * 100, f"same week last year (${ly:,.0f})"
    elif avg4:
        chg, basis = (net - avg4) / avg4 * 100, f"its prior {len(prior)}-week average (${avg4:,.0f}); no last-year week to compare"
    else:
        return {"status": INFO, "value": f"${net:,.0f}", "target": "nothing to compare against yet"}
    status = EXCEED if chg >= SALES_EXCEED else ON if chg >= SALES_OFF else OFF
    coach = []
    if ly and ly > mn * 3:
        short = sorted([(n - l, d, n, l) for d, n, l in rows if l > mn and n < l])[:3]
        if short and status != EXCEED:
            coach.append("Biggest gaps to last year: " + "; ".join(
                f"{d:%a %b %-d} ${n:,.0f} vs ${l:,.0f}" for _, d, n, l in short))
    tr = []
    for s, n, l in trend:
        if n is None:
            tr.append((s, "n/a"))
        elif l and l > mn * 3:
            tr.append((s, f"${n:,.0f} ({(n - l) / l * 100:+.0f}% YoY)"))
        else:
            tr.append((s, f"${n:,.0f}"))
    return {"status": status, "value": f"${net:,.0f} ({chg:+.1f}%)", "target": f"vs {basis}",
            "trend": tr, "coach": coach,
            "note": "Sales side only. The monthly income statement is not connected yet."}


_lb_cache: dict = {}


def _logbook_week(venue, week):
    key = (venue, week)
    if key in _lb_cache:
        return _lb_cache[key]
    token = _safe(LB._token)
    out = None
    if token:
        mn = VENUES[venue]["min_net"]
        trading = [d for d in days(week) if (pos_net(venue, d) or 0) > mn]
        res = [(d, logbook_day(venue, d, token)) for d in trading]
        if trading and all(r is not None for _, r in res):
            out = res
    _lb_cache[key] = out
    return out


def m_logbook(venue, week):
    res = _logbook_week(venue, week)
    if res is None:
        return {"status": NODATA, "value": "logbook could not be read", "target": ""}
    done = [d for d, r in res if r[0]]
    missed = [d for d, r in res if not r[0]]
    pct = len(done) / len(res) * 100
    coach = ["No logbook entry for: " + ", ".join(f"{d:%a %b %-d}" for d in missed)] if missed else []
    return {"status": ON if pct >= LOGBOOK_ON else OFF, "value": f"{len(done)} of {len(res)} trading nights logged",
            "target": f"≥{LOGBOOK_ON:.0f}%", "coach": coach,
            "note": "Measures one process only: the nightly manager logbook."}


def m_guest_issues(venue, week):
    res = _logbook_week(venue, week)
    if res is None:
        return {"status": NODATA, "value": "logbook could not be read", "target": ""}
    n = sum(1 for _, r in res if r[1])
    return {"status": INFO, "value": f"{n} of {len(res)} nights had a guest issue logged", "target": "",
            "note": "Shown for context. Review ratings are not connected, so this is not graded."}


MEASURES = {"labour": m_labour, "sales": m_sales, "logbook": m_logbook, "guest_issues": m_guest_issues}


# ------------------------------------------------------------------ assemble
def review_person(p, week):
    items = []
    for a in p["accountabilities"]:
        fn = MEASURES.get(a.get("measure") or "")
        r = fn(p["venue"], week) if fn else {"status": NODATA, "value": "nothing connected", "target": ""}
        items.append({"name": a["name"], **r})
    graded = [i for i in items if i["status"] in (EXCEED, ON, OFF)]
    off = [i for i in graded if i["status"] == OFF]
    exc = [i for i in graded if i["status"] == EXCEED]
    if not graded:
        overall = NODATA
    elif off:
        overall = OFF
    elif len(exc) * 2 >= len(graded):
        overall = EXCEED
    else:
        overall = ON
    return {"person": p, "items": items, "overall": overall, "graded": len(graded), "off": len(off), "exceed": len(exc)}


COL = {EXCEED: "#15803d", ON: "#1f3a5f", OFF: "#b91c1c", NODATA: "#9ca3af", INFO: "#6b7280"}


def _pill(s):
    return (f"<span style='background:{COL[s]};color:#fff;border-radius:10px;padding:2px 9px;"
            f"font-size:12px;white-space:nowrap'>{s}</span>")


def render(results, week):
    end = week + dt.timedelta(days=6)
    e = html.escape
    out = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<meta name='robots' content='noindex'><title>Accountability review</title>",
           "<style>body{font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1f2937;max-width:860px;margin:0 auto;padding:16px;background:#f8fafc}"
           "h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0}table{border-collapse:collapse;width:100%}"
           "td,th{padding:7px 8px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;font-size:14px}"
           "th{font-size:12px;color:#6b7280;font-weight:600}.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:14px 0}"
           ".m{color:#6b7280;font-size:13px}.c{background:#fff7ed;border-left:3px solid #f59e0b;padding:8px 10px;margin-top:8px;font-size:13px}"
           ".scroll{overflow-x:auto}</style></head><body>",
           f"<h1>Accountability review</h1><div class='m'>Week of {week:%a %b %-d} to {end:%a %b %-d, %Y}. "
           f"First names only. Generated {dt.datetime.now():%Y-%m-%d %H:%M}.</div>",
           "<div class='card'><div class='scroll'><table><tr><th>Person</th><th>Seat</th><th>Overall</th><th>Graded</th></tr>"]
    for r in results:
        p = r["person"]
        out.append(f"<tr><td><b>{e(p['name'])}</b></td><td>{e(p['seat'])}</td><td>{_pill(r['overall'])}</td>"
                   f"<td>{r['exceed']} exceeding, {r['off']} off track, of {r['graded']} graded "
                   f"({len(r['items'])} accountabilities)</td></tr>")
    out.append("</table></div></div>")
    for r in results:
        p = r["person"]
        out.append(f"<div class='card'><h2>{e(p['name'])} {_pill(r['overall'])}</h2><div class='m'>{e(p['seat'])}</div>"
                   "<div class='scroll'><table><tr><th>Accountability</th><th>Status</th><th>This week</th><th>Goal / comparison</th></tr>")
        for i in r["items"]:
            out.append(f"<tr><td>{e(i['name'])}</td><td>{_pill(i['status'])}</td><td>{e(i['value'])}</td><td>{e(i.get('target', ''))}</td></tr>")
        out.append("</table></div>")
        for i in r["items"]:
            if i.get("trend"):
                out.append(f"<div class='m' style='margin-top:8px'><b>{e(i['name'])}, last {len(i['trend'])} weeks:</b> " +
                           " · ".join(f"{s:%b %-d}: {e(v)}" for s, v in reversed(i["trend"])) + "</div>")
        for i in r["items"]:
            if i.get("coach"):
                out.append(f"<div class='c'><b>{e(i['name'])}:</b> " + " ".join(e(c) for c in i["coach"]) + "</div>")
        notes = [f"{i['name']}: {i['note']}" for i in r["items"] if i.get("note")]
        if notes:
            out.append("<div class='m' style='margin-top:8px'>" + " ".join(e(n) for n in notes) + "</div>")
        out.append("</div>")
    out.append("<div class='m'>How it is graded: Labour = week's labour % from the Labour 2026 sheet vs its goal "
               f"(exceeding = {LABOUR_EXCEED_PTS:g} point or more under). Income Statement = POS net sales vs the same week last year "
               f"(exceeding = +{SALES_EXCEED:g}% or better, off track = worse than {SALES_OFF:g}%). Processes = share of trading nights "
               f"with a manager logbook entry (on track = {LOGBOOK_ON:g}% or more). A venue's number is shared by everyone "
               "accountable for it, so it shows the result, not who caused it.</div></body></html>")
    return "\n".join(out)


def write_ledger(results, week):
    path = HERE / "data" / "ledger.csv"
    rows = []
    if path.exists():
        with path.open() as f:
            rows = [r for r in csv.DictReader(f) if r["week"] != week.isoformat()]
    for r in results:
        for i in r["items"]:
            rows.append({"week": week.isoformat(), "person": r["person"]["name"], "seat": r["person"]["seat"],
                         "accountability": i["name"], "status": i["status"], "value": i["value"],
                         "target": i.get("target", ""), "overall": r["overall"]})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["week", "person", "seat", "accountability", "status", "value", "target", "overall"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["week"], x["person"])))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="Monday of the week to review (YYYY-MM-DD); default = last completed week")
    args = ap.parse_args()
    today = dt.date.today()
    week = dt.date.fromisoformat(args.week) if args.week else today - dt.timedelta(days=today.weekday() + 7)
    people = json.loads((HERE / "seats.json").read_text())["people"]
    results = []
    for p in people:
        print(f"{p['name']} ...", file=sys.stderr)
        results.append(review_person(p, week))
    ledger = write_ledger(results, week)
    report = HERE / "reports" / f"accountability-review-{week.isoformat()}.html"
    report.write_text(render(results, week))
    (HERE / "index.html").write_text(report.read_text())  # GitHub Pages shows the latest week
    for r in results:
        print(f"{r['person']['name']:<18} {r['overall']:<10} " +
              " | ".join(f"{i['name']}: {i['status']} {i['value']}" for i in r["items"] if i["status"] != NODATA))
    print(f"\nledger: {ledger}\nreport: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
