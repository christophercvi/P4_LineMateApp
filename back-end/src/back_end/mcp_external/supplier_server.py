"""Supplier inventory MCP server (stdio). Stands in for the restaurant supplier's ordering system.

Run on its own:  uv run python -m back_end.mcp_external.supplier_server
LineMate's triage graph calls `check_stock` and `price_quote`; it never places orders itself -
orders become `draft_supply_order` approvals that the Kitchen Manager signs off.
"""

from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer

SUPPLIER = "Coastal Restaurant Supply"

CATALOG: list[dict[str, Any]] = [
    {
        "sku": "CAN-OIL-35",
        "item": "Canola frying oil, 35 lb",
        "unit": "case",
        "on_hand": 1,
        "par": 4,
        "unit_price": 42.50,
        "lead_days": 2,
        "keywords": ["fryer oil", "frying oil", "canola", "fryer"],
    },
    {
        "sku": "PB-18-100",
        "item": "Disposable piping bags 18 in (100)",
        "unit": "case",
        "on_hand": 1,
        "par": 3,
        "unit_price": 31.00,
        "lead_days": 3,
        "keywords": ["piping bag", "piping bags"],
    },
    {
        "sku": "GB-WIRE-18",
        "item": "Grill brush, 18 in stainless",
        "unit": "each",
        "on_hand": 2,
        "par": 6,
        "unit_price": 14.75,
        "lead_days": 2,
        "keywords": ["grill brush", "grill brushes", "brush"],
    },
    {
        "sku": "CB-COLOR-6",
        "item": "Colour-coded cutting boards (set of 6)",
        "unit": "set",
        "on_hand": 0,
        "par": 2,
        "unit_price": 89.00,
        "lead_days": 4,
        "keywords": ["cutting board", "cutting boards", "colour code", "color code"],
    },
    {
        "sku": "ST-FULL-12",
        "item": "Full sheet trays, aluminium (12)",
        "unit": "case",
        "on_hand": 3,
        "par": 4,
        "unit_price": 64.00,
        "lead_days": 3,
        "keywords": ["sheet tray", "sheet trays"],
    },
    {
        "sku": "SB-24-12",
        "item": "Squeeze bottles 24 oz (12)",
        "unit": "case",
        "on_hand": 4,
        "par": 4,
        "unit_price": 18.00,
        "lead_days": 2,
        "keywords": ["squeeze bottle", "squeeze bottles"],
    },
    {
        "sku": "CHK-BONE-40",
        "item": "Chicken bones for stock, 40 lb",
        "unit": "case",
        "on_hand": 2,
        "par": 3,
        "unit_price": 38.00,
        "lead_days": 1,
        "keywords": ["chicken stock", "chicken bones", "stock yield"],
    },
    {
        "sku": "SAN-QUAT-1G",
        "item": "Quat sanitizer concentrate, 1 gal",
        "unit": "jug",
        "on_hand": 3,
        "par": 4,
        "unit_price": 22.00,
        "lead_days": 2,
        "keywords": ["sanitizer", "quat"],
    },
    {
        "sku": "LBL-DAY-500",
        "item": "Day-dot labels (500)",
        "unit": "roll",
        "on_hand": 2,
        "par": 5,
        "unit_price": 9.50,
        "lead_days": 2,
        "keywords": ["label", "labels", "day dot"],
    },
    {
        "sku": "GLV-NIT-L",
        "item": "Nitrile gloves, large (1000)",
        "unit": "case",
        "on_hand": 6,
        "par": 4,
        "unit_price": 54.00,
        "lead_days": 2,
        "keywords": ["gloves", "nitrile"],
    },
]

server = MCPServer(
    "supplier-inventory",
    instructions="Stock levels and price quotes from Coastal Restaurant Supply. Read-only: no orders are placed.",
)


def _find(query: str) -> tuple[dict[str, Any] | None, str | None]:
    """Returns the catalogue item and the keyword that matched it."""
    q = query.lower().strip()
    for item in CATALOG:
        if q == item["sku"].lower():
            return item, item["keywords"][0]
    best, best_kw = None, None
    for item in CATALOG:
        for kw in item["keywords"]:
            if kw in q and len(kw) > len(best_kw or ""):
                best, best_kw = item, kw
    return best, best_kw


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "supplier": SUPPLIER,
        "sku": item["sku"],
        "item": item["item"],
        "unit": item["unit"],
        "on_hand": item["on_hand"],
        "par": item["par"],
        "below_par": item["on_hand"] < item["par"],
        "shortfall": max(0, item["par"] - item["on_hand"]),
        "unit_price": item["unit_price"],
        "lead_days": item["lead_days"],
    }


@server.tool(description="Look up stock for a SKU or a plain-language item (e.g. 'fryer oil'). Returns on-hand vs par.")
def check_stock(query: str) -> dict[str, Any]:
    item, keyword = _find(query)
    if item is None:
        return {"found": False, "query": query, "supplier": SUPPLIER}
    return {"found": True, "matched": keyword, **_public(item)}


@server.tool(description="Quote a quantity of a SKU with the earliest delivery date. Does not place an order.")
def price_quote(sku: str, quantity: int) -> dict[str, Any]:
    item, _ = _find(sku)
    if item is None:
        return {"found": False, "sku": sku, "supplier": SUPPLIER}
    qty = max(1, int(quantity))
    return {
        "found": True,
        **_public(item),
        "quantity": qty,
        "total": round(item["unit_price"] * qty, 2),
        "delivery_date": (date.today() + timedelta(days=item["lead_days"])).isoformat(),
    }


@server.tool(description="List catalogue items that are below par.")
def below_par() -> dict[str, Any]:
    return {"supplier": SUPPLIER, "items": [_public(i) for i in CATALOG if i["on_hand"] < i["par"]]}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
