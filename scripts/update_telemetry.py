"""
Pulls real GitHub stats for `sahillroy` and regenerates telemetry.svg from
telemetry.template.svg. Run manually with:

    GH_TOKEN=your_token_here python scripts/update_telemetry.py

Or via the scheduled GitHub Action (.github/workflows/update-telemetry.yml),
which supplies GH_TOKEN from the repo secret automatically.
"""

import os
import sys
import datetime
import requests

USERNAME = "sahillroy"
TOKEN = os.environ.get("GH_TOKEN")

if not TOKEN:
    sys.exit("GH_TOKEN environment variable is not set. See README-AUTOMATION.md.")

HEADERS = {"Authorization": f"bearer {TOKEN}"}
GRAPHQL_URL = "https://api.github.com/graphql"
REST_URL = "https://api.github.com"


def gh_graphql(query, variables=None):
    resp = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables or {}}, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        sys.exit(f"GraphQL error: {data['errors']}")
    return data["data"]


def get_account_created_year():
    query = """
    query($login: String!) {
      user(login: $login) { createdAt }
    }
    """
    data = gh_graphql(query, {"login": USERNAME})
    created = data["user"]["createdAt"]
    return int(created[:4])


def get_contributions_for_year(from_date, to_date):
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            weeks {
              contributionDays { date contributionCount }
            }
          }
        }
      }
    }
    """
    data = gh_graphql(query, {"login": USERNAME, "from": from_date, "to": to_date})
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = []
    for w in weeks:
        for d in w["contributionDays"]:
            days.append((d["date"], d["contributionCount"]))
    return days


def get_all_contribution_days():
    start_year = get_account_created_year()
    this_year = datetime.date.today().year
    all_days = []
    for year in range(start_year, this_year + 1):
        from_dt = f"{year}-01-01T00:00:00Z"
        to_dt = f"{year}-12-31T23:59:59Z"
        all_days.extend(get_contributions_for_year(from_dt, to_dt))
    # dedupe + sort
    seen = {}
    for date_str, count in all_days:
        seen[date_str] = count
    today = datetime.date.today()
    # GitHub returns the full calendar year including future dates padded
    # with 0 contributions — drop anything after today so "last 30 days"
    # actually means the last 30 real days, not future placeholders.
    filtered = {d: c for d, c in seen.items() if datetime.date.fromisoformat(d) <= today}
    return sorted(filtered.items())


def compute_streaks(days):
    total = sum(count for _, count in days)
    today = datetime.date.today()

    # current streak: walk backward from today (or yesterday if today has 0 so far)
    by_date = {datetime.date.fromisoformat(d): c for d, c in days}
    current_streak = 0
    current_start = None
    cursor = today
    while by_date.get(cursor, 0) > 0:
        if current_start is None:
            current_start = cursor
        current_streak += 1
        cursor -= datetime.timedelta(days=1)
    current_end = today if current_streak else None
    # streak runs backward from today to current_start; display range is current_start..today
    current_range = ""
    if current_streak:
        current_range = f"{cursor + datetime.timedelta(days=1):%b %d} – {today:%b %d}"

    # longest streak: scan all days in order
    longest = 0
    longest_start = longest_end = None
    run = 0
    run_start = None
    for date_str, count in days:
        d = datetime.date.fromisoformat(date_str)
        if count > 0:
            if run == 0:
                run_start = d
            run += 1
            if run > longest:
                longest = run
                longest_start = run_start
                longest_end = d
        else:
            run = 0
    longest_range = ""
    if longest:
        if longest_start.year == longest_end.year:
            longest_range = f"{longest_start:%b %d} – {longest_end:%b %d}, {longest_end.year}"
        else:
            longest_range = f"{longest_start:%b %d, %Y} – {longest_end:%b %d, %Y}"

    first_active = next((d for d, c in days if c > 0), days[0][0])
    total_range = f"{datetime.date.fromisoformat(first_active):%b %-d, %Y} – present"

    return {
        "TOTAL_CONTRIB": str(total),
        "TOTAL_CONTRIB_RANGE": total_range,
        "CURRENT_STREAK": str(current_streak),
        "CURRENT_STREAK_RANGE": current_range or "—",
        "LONGEST_STREAK": str(longest),
        "LONGEST_STREAK_RANGE": longest_range or "—",
    }


def get_repo_stats():
    repos = []
    page = 1
    while True:
        resp = requests.get(
            f"{REST_URL}/users/{USERNAME}/repos",
            params={"per_page": 100, "page": page, "type": "owner"},
            headers=HEADERS,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
    total_stars = sum(r["stargazers_count"] for r in repos)
    return {"REPO_COUNT": str(len(repos)), "STARS": str(total_stars)}


def build_contribution_chart(days):
    last30 = days[-30:] if len(days) >= 30 else days
    values = [c for _, c in last30]
    n = len(last30)
    max_v = max(values) if values and max(values) > 0 else 1

    x0, x1 = 60, 1090
    y_base, y_top = 900, 740
    step = (x1 - x0) / (n - 1) if n > 1 else 0

    def y_for(v):
        return y_base - (v / max_v) * (y_base - y_top)

    points = " ".join(f"{x0 + i*step:.1f},{y_for(v):.1f}" for i, v in enumerate(values))

    grid = []
    for frac in (0, 0.5, 1.0):
        y = y_for(frac * max_v)
        grid.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="var(--rule)" stroke-width="1" stroke-dasharray="3 3" opacity=".35"/>')
        grid.append(f'<text fill="var(--dim)" x="{x0 - 15}" y="{y + 4:.1f}" font-size="10" text-anchor="end">{round(frac * max_v)}</text>')

    day_labels = []
    label_every = max(1, n // 8)
    for i, (d, _) in enumerate(last30):
        if i % label_every == 0 or i == n - 1:
            x = x0 + i * step
            day_short = datetime.date.fromisoformat(d).strftime("%-d")
            day_labels.append(f'<text fill="var(--dim)" x="{x:.1f}" y="{y_base + 20}" font-size="9" text-anchor="middle">{day_short}</text>')

    last_x = x0 + (n - 1) * step
    last_y = y_for(values[-1]) if values else y_base

    parts = grid + [
        f'<line x1="{x0}" y1="{y_base}" x2="{x1}" y2="{y_base}" stroke="var(--rule)" stroke-width="1"/>',
        f'<polyline points="{points}" stroke="var(--accent)" stroke-width="1.5" fill="none"/>',
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="var(--accent)"/>',
    ] + day_labels + [
        f'<text fill="var(--dim)" x="{x0}" y="{y_base + 44}" font-size="11" letter-spacing="1.5">last 30 days</text>',
    ]
    return "\n    ".join(parts)


def main():
    days = get_all_contribution_days()
    values = compute_streaks(days)
    values.update(get_repo_stats())
    values["CONTRIB_CHART"] = build_contribution_chart(days)

    template_path = os.path.join(os.path.dirname(__file__), "..", "assets", "telemetry.template.svg")
    output_path = os.path.join(os.path.dirname(__file__), "..", "assets", "telemetry.svg")

    with open(template_path) as f:
        svg = f.read()

    for key, val in values.items():
        svg = svg.replace("{{" + key + "}}", val)

    with open(output_path, "w") as f:
        f.write(svg)

    print("telemetry.svg updated with:", values)


if __name__ == "__main__":
    main()
