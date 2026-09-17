"""Role-based permission rules shared by the REST API, the LangGraph agents and the MCP server.

The front end keeps a mirror of these rules only to decide what to show; every write is checked
here on the server.
"""

from dataclasses import dataclass
from typing import Literal

Role = Literal["line_cook", "sous_chef", "kitchen_manager", "admin", "service"]
ROLES: tuple[Role, ...] = ("line_cook", "sous_chef", "kitchen_manager", "admin", "service")
CHAT_ROLES: tuple[Role, ...] = ("line_cook", "sous_chef", "kitchen_manager", "admin")
KITCHEN_ROLES: tuple[Role, ...] = ("line_cook", "sous_chef", "kitchen_manager")

ROLE_LABEL: dict[str, str] = {
    "line_cook": "Line Cook",
    "sous_chef": "Sous Chef",
    "kitchen_manager": "Kitchen Manager",
    "admin": "Admin",
    "service": "Service (MCP)",
}

PRIORITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: str
    role: Role
    station: str | None
    crew_member_id: str | None
    display_name: str = ""

    @property
    def who(self) -> str:
        """Identifier written into activity, comments and reviews (crew id when the user is crew)."""
        return self.crew_member_id or self.user_id


class can:
    @staticmethod
    def ask(a: Actor) -> bool:
        return a.role != "service"

    @staticmethod
    def see_incident_reports(a: Actor) -> bool:
        return a.role != "line_cook"

    @staticmethod
    def create_ticket(a: Actor) -> bool:
        return a.role in KITCHEN_ROLES or a.role == "service"

    @staticmethod
    def comment(a: Actor) -> bool:
        return a.role in KITCHEN_ROLES

    @staticmethod
    def attach_to_ticket(a: Actor) -> bool:
        return a.role in KITCHEN_ROLES

    @staticmethod
    def raise_priority(a: Actor) -> bool:
        return a.role in KITCHEN_ROLES

    @staticmethod
    def lower_priority_or_close(a: Actor, ticket_station: str) -> bool:
        return a.role == "kitchen_manager" or (a.role == "sous_chef" and a.station == ticket_station)

    @staticmethod
    def change_status(a: Actor, ticket_station: str, assignee_id: str | None) -> bool:
        return (
            a.role == "kitchen_manager"
            or (a.role == "sous_chef" and a.station == ticket_station)
            or (a.role == "line_cook" and assignee_id is not None and assignee_id == a.crew_member_id)
        )

    @staticmethod
    def assign(a: Actor, ticket_station: str) -> bool:
        return a.role == "kitchen_manager" or (a.role == "sous_chef" and a.station == ticket_station)

    @staticmethod
    def upload_category(a: Actor, category: str, station: str | None) -> bool:
        if a.role == "kitchen_manager":
            return True
        if a.role == "sous_chef":
            return category != "sop" and station == a.station
        return False

    @staticmethod
    def upload_any(a: Actor) -> bool:
        return a.role in ("kitchen_manager", "sous_chef")

    @staticmethod
    def mark_reviewed(a: Actor, doc_station: str) -> bool:
        return a.role == "kitchen_manager" or (a.role == "sous_chef" and a.station == doc_station)

    @staticmethod
    def archive_document(a: Actor) -> bool:
        return a.role == "kitchen_manager"

    @staticmethod
    def view_audits(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "admin")

    @staticmethod
    def view_ownership_audit(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager")

    @staticmethod
    def view_analytics(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "admin")

    @staticmethod
    def analytics_all_stations(a: Actor) -> bool:
        return a.role in ("kitchen_manager", "admin")

    @staticmethod
    def run_triage(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager")

    @staticmethod
    def view_approvals(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "admin")

    @staticmethod
    def approve(a: Actor) -> bool:
        return a.role == "kitchen_manager"

    @staticmethod
    def request_approval(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "service")

    @staticmethod
    def view_agent_runs(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "admin")

    @staticmethod
    def view_all_runs(a: Actor) -> bool:
        return a.role == "admin"

    @staticmethod
    def view_mcp(a: Actor) -> bool:
        return a.role in ("sous_chef", "kitchen_manager", "admin")

    @staticmethod
    def call_mcp_tool(a: Actor, roles: tuple[str, ...] | list[str]) -> bool:
        return a.role in roles

    @staticmethod
    def manage_tokens(a: Actor) -> bool:
        return a.role == "admin"

    @staticmethod
    def admin(a: Actor) -> bool:
        return a.role == "admin"


DenyKind = Literal["upload", "approve", "status", "lower", "assign", "review", "archive"]


def deny_reason(kind: DenyKind, a: Actor) -> str:
    who = ROLE_LABEL[a.role]
    match kind:
        case "upload":
            if a.role == "sous_chef":
                return (
                    "Sous Chefs can upload Recipes, Onboarding and Incident Reports for their own station. "
                    "SOPs are published by the Kitchen Manager."
                )
            return f"{who}s can't publish to the knowledge base. Raise a ticket or comment instead."
        case "approve":
            if a.role == "admin":
                return "Separation of duties: admins run the system but do not approve kitchen actions."
            return "Only the Kitchen Manager can approve. You can view the request and add context."
        case "status":
            return "You can change the status of tickets assigned to you. Sous Chefs manage their own station."
        case "lower":
            return "Anyone can escalate, but only a Sous Chef (own station) or the Kitchen Manager can lower priority or close."
        case "assign":
            return "Assigning is done by the Sous Chef of the station or the Kitchen Manager."
        case "review":
            return "Reviews are signed off by the station's Sous Chef or the Kitchen Manager."
        case "archive":
            return "Only the Kitchen Manager can archive documents."
