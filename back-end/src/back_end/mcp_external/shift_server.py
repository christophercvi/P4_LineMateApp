"""Shift scheduler MCP server (stdio). Stands in for the restaurant's scheduling system.

Run on its own:  uv run python -m back_end.mcp_external.shift_server
LineMate's triage graph calls `get_roster` (who is on per station) and `find_cover` (who could
pick up a short-staffed shift).
"""

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

CREW_FILE = Path(__file__).resolve().parents[3] / "seed_data" / "crew.json"

# Weekly pattern: crew id -> shifts worked. "AM" 06:00-14:00, "PM" 14:00-22:00.
PATTERN: dict[str, dict[str, list[str]]] = {
    "CM-01": {"mon": ["PM"], "tue": ["PM"], "wed": ["PM"], "fri": ["PM"], "sat": ["PM"]},
    "CM-02": {"mon": ["AM"], "tue": ["AM"], "thu": ["AM", "PM"], "fri": ["AM"], "sat": ["PM"]},
    "CM-03": {"wed": ["AM"], "thu": ["AM"], "sat": ["AM"], "sun": ["AM", "PM"]},
    "CM-04": {"mon": ["AM"], "wed": ["AM"], "thu": ["AM"], "sat": ["AM"], "sun": ["AM"]},
    "CM-05": {"tue": ["AM"], "wed": ["AM"], "fri": ["AM"], "sat": ["AM"], "sun": ["AM"]},
    "CM-06": {"mon": ["AM"], "tue": ["AM"], "wed": ["AM"], "thu": ["AM"], "fri": ["AM"]},
    "CM-07": {"tue": ["PM"], "wed": ["PM"], "fri": ["AM"], "sat": ["AM", "PM"]},
    "CM-08": {"mon": ["PM"], "thu": ["PM"], "fri": ["PM"], "sat": ["PM"], "sun": ["PM"]},
    "CM-09": {"wed": ["PM"], "thu": ["PM"], "fri": ["PM"], "sat": ["PM"], "sun": ["PM"]},
    "CM-10": {"mon": ["PM"], "tue": ["PM"], "fri": ["PM"], "sat": ["PM"]},
    "CM-11": {"mon": ["AM"], "tue": ["AM"], "wed": ["AM"], "thu": ["AM"], "fri": ["AM", "PM"]},
    "CM-12": {"thu": ["PM"], "fri": ["PM"], "sat": ["PM"], "sun": ["PM"]},
}
# Cross-training: who can cover a station other than their own.
CROSS_TRAINED: dict[str, list[str]] = {"CM-03": ["grill"], "CM-07": ["grill", "prep"], "CM-12": ["grill"]}
NEEDED = {"grill": 4, "pastry": 2, "prep": 3, "foh": 3}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

server = MCPServer("shift-scheduler", instructions="Read-only roster and cover availability for the kitchen.")


def _crew() -> dict[str, dict[str, Any]]:
    try:
        return {c["id"]: c for c in json.loads(CREW_FILE.read_text())}
    except FileNotFoundError:
        return {}


def _day(day: str | None) -> str:
    d = (day or "fri").lower()[:3]
    return d if d in DAYS else "fri"


@server.tool(description="Who is scheduled at a station on a day ('mon'..'sun') and shift ('AM' or 'PM').")
def get_roster(station: str, day: str = "fri", shift: str = "PM") -> dict[str, Any]:
    crew = _crew()
    d, sh = _day(day), shift.upper()
    on = [
        {"crew_id": cid, "name": crew.get(cid, {}).get("name", cid), "title": crew.get(cid, {}).get("title", "")}
        for cid, week in PATTERN.items()
        if crew.get(cid, {}).get("station") == station and sh in week.get(d, [])
    ]
    needed = NEEDED.get(station, 2)
    return {"station": station, "day": d, "shift": sh, "scheduled": on, "needed": needed, "short_by": max(0, needed - len(on))}


@server.tool(description="Crew who are off that shift and trained for the station, best options first.")
def find_cover(station: str, day: str = "fri", shift: str = "PM") -> dict[str, Any]:
    crew = _crew()
    d, sh = _day(day), shift.upper()
    options = []
    for cid, member in crew.items():
        if sh in PATTERN.get(cid, {}).get(d, []):
            continue
        same = member.get("station") == station
        if same or station in CROSS_TRAINED.get(cid, []):
            hours = sum(len(v) for v in PATTERN.get(cid, {}).values()) * 8
            options.append(
                {
                    "crew_id": cid,
                    "name": member["name"],
                    "home_station": member.get("station"),
                    "cross_trained": not same,
                    "hours_this_week": hours,
                }
            )
    options.sort(key=lambda o: (o["cross_trained"], o["hours_this_week"]))
    return {"station": station, "day": d, "shift": sh, "options": options[:4]}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
