#!/usr/bin/env python3
"""Fetch contribution data + featured-repo metadata from GitHub and write
contribution_data.json. Runs daily in the Action; needs GITHUB_TOKEN and GH_USERNAME.
Fails loudly (non-zero exit) rather than writing placeholder numbers."""
from __future__ import annotations
import calendar, json, os, sys
import datetime as dt
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.github.com/graphql"
DAY = dt.timedelta(days=1)


# ---- pure helpers (also imported by render_cards.py for SAMPLE=1) -----------
def week_rows(calendar_weeks):
    """GraphQL weeks -> [{"s": sunday-iso, "c": [7 counts or None]}]."""
    rows = []
    for wk in calendar_weeks:
        c, sunday = [None] * 7, None
        for day in wk["contributionDays"]:
            d, wd = dt.date.fromisoformat(day["date"]), day["weekday"]  # 0 = Sunday
            c[wd] = day["contributionCount"]
            sunday = d - dt.timedelta(days=wd)
        if sunday:
            rows.append({"s": sunday.isoformat(), "c": c})
    return rows


def day_list(rows):
    out = []
    for wk in rows:
        s = dt.date.fromisoformat(wk["s"])
        out += [(s + dt.timedelta(days=i), c) for i, c in enumerate(wk["c"]) if c is not None]
    return out


def month_buckets(days, today):
    keys, y, m = [], today.year, today.month
    for _ in range(12):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    totals = defaultdict(int)
    for d, c in days:
        totals[(d.year, d.month)] += c
    return [{"key": f"{y}-{m:02d}", "label": calendar.month_abbr[m], "count": totals[(y, m)]} for y, m in keys]


def streaks(days, today):
    counts = {d: c for d, c in days}
    d = today if counts.get(today, 0) > 0 else today - DAY  # today may simply not be done yet
    cur = 0
    while counts.get(d, 0) > 0:
        cur += 1
        d -= DAY
    best = run = 0
    best_end = None
    for d, c in sorted(days):
        if c > 0:
            run += 1
            if run > best:
                best, best_end = run, d
        else:
            run = 0
    return cur, best, best_end


def summarize(grid_rows, month_rows, today):
    gdays = day_list(grid_rows)
    cur, best, best_end = streaks(gdays, today)
    peak = max(gdays, key=lambda x: (x[1], x[0]), default=None)
    longest = None
    if best_end and best:
        longest = {"days": best, "from": (best_end - dt.timedelta(days=best - 1)).isoformat(),
                   "to": best_end.isoformat()}
    return {
        "total": sum(c for _, c in gdays),
        "current_streak": cur,
        "longest_streak": best,
        "longest": longest,
        "active_days": sum(1 for _, c in gdays if c > 0),
        "busiest": {"date": peak[0].isoformat(), "count": peak[1]} if peak and peak[1] > 0 else None,
        "months": month_buckets(day_list(month_rows), today),
        "grid": grid_rows,
    }


# ---- GitHub ----------------------------------------------------------------
def gql(query, variables, token, tolerate_missing=False):
    req = Request(ENDPOINT, data=json.dumps({"query": query, "variables": variables}).encode(),
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
    except HTTPError as e:
        sys.exit(f"GitHub API error {e.code}: {e.read().decode()[:400]}")
    errs = res.get("errors") or []
    if errs:
        # The Action's GITHUB_TOKEN only sees this repository, so a private
        # featured repo resolves to NOT_FOUND. Skip those, fail on anything else.
        if tolerate_missing and all(e.get("type") == "NOT_FOUND" for e in errs):
            for e in errs:
                print(f"warning: {e.get('message')}", file=sys.stderr)
            return res.get("data") or {}
        sys.exit(f"GraphQL errors: {errs}")
    return res["data"]


CAL = "contributionCalendar{totalContributions weeks{contributionDays{date contributionCount weekday}}}"


def main():
    token, login = os.environ.get("GITHUB_TOKEN"), os.environ.get("GH_USERNAME")
    if not token or not login:
        sys.exit("GITHUB_TOKEN and GH_USERNAME must be set")
    profile = json.loads((ROOT / "profile.json").read_text())
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    y, m = today.year, today.month - 11
    if m < 1:
        y, m = y - 1, m + 12
    month_from = dt.date(y, m, 1)
    grid_from = today - dt.timedelta(days=364)
    iso = lambda d: f"{d.isoformat()}T00:00:00Z"
    q = ("query($l:String!,$mf:DateTime!,$gf:DateTime!,$to:DateTime!){user(login:$l){"
         f"months:contributionsCollection(from:$mf,to:$to){{{CAL}}}"
         f"grid:contributionsCollection(from:$gf,to:$to){{{CAL}}}}}}}")
    u = gql(q, {"l": login, "mf": iso(month_from), "gf": iso(grid_from), "to": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, token)["user"]
    out = summarize(week_rows(u["grid"]["contributionCalendar"]["weeks"]),
                    week_rows(u["months"]["contributionCalendar"]["weeks"]), today)

    # account facts for the summary card (joined, public repos, where)
    aq = ("query($l:String!){user(login:$l){name createdAt location "
          "repositories(privacy:PUBLIC,ownerAffiliations:OWNER){totalCount}}}")
    a = gql(aq, {"l": login}, token)["user"]
    joined = dt.date.fromisoformat(a["createdAt"][:10])
    out["account"] = {
        "name": a.get("name") or login,
        "joined": joined.isoformat(),
        "public_repos": a["repositories"]["totalCount"],
        "location": a.get("location"),
    }

    # lifetime contributions, one call per calendar year since the account opened
    lifetime = 0
    for year in range(joined.year, today.year + 1):
        f = max(joined, dt.date(year, 1, 1))
        t = min(today, dt.date(year, 12, 31))
        if f > t:
            continue
        qy = ("query($l:String!,$f:DateTime!,$t:DateTime!){user(login:$l){"
              "contributionsCollection(from:$f,to:$t){contributionCalendar{totalContributions}}}}")
        year_total = gql(qy, {"l": login, "f": iso(f), "t": iso(t)}, token)["user"][
            "contributionsCollection"]["contributionCalendar"]["totalContributions"]
        lifetime += year_total
    out["all_time"] = {"total": lifetime, "since": joined.isoformat()}

    slugs = [r["slug"] for r in profile["projects"] if r.get("slug")]
    out["repos"] = {}
    if slugs:
        frags = " ".join(
            f'r{i}:repository(owner:{json.dumps(s.split("/")[0])},name:{json.dumps(s.split("/")[1])})'
            "{stargazerCount primaryLanguage{name color}}" for i, s in enumerate(slugs))
        data = gql("query{" + frags + "}", {}, token, tolerate_missing=True)
        for i, s in enumerate(slugs):
            r = data.get(f"r{i}")
            if r:
                lang = r.get("primaryLanguage") or {}
                out["repos"][s] = {"stars": r["stargazerCount"], "language": lang.get("name"), "color": lang.get("color")}
    (ROOT / "contribution_data.json").write_text(json.dumps(out, indent=1))
    print(f"@{login}: total={out['total']} streak={out['current_streak']} active={out['active_days']} lifetime={out['all_time']['total']} repos={len(out['repos'])}")


if __name__ == "__main__":
    main()
