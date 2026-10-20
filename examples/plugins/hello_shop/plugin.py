"""Hello Shop: a complete, small plugin to copy from (docs/PLUGINS.md).

It reads a pretend shop from shop.json next to this file (a real plugin reads its project's database or API, with the
address and key from its settings). It gives agents two read tools, one action, two numbers and a starter template.
To try it: copy this folder to plugins/hello_shop, restart, and switch it on in Admin → Plugins.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.plugin_api import Action, Context, Metric, Param, Plugin, Setting, Template, Tool

DATA = Path(__file__).with_name("shop.json")


def _shop() -> dict[str, Any]:
    return json.loads(DATA.read_text())


def _featured(ctx: Context) -> list[str]:
    path = ctx.data_dir / "featured.json"
    return json.loads(path.read_text()) if path.exists() else []


# --- tools: what agents may read --------------------------------------------------------------------------------
def products(ctx: Context, low_stock_below: int = 0) -> dict[str, Any]:
    items = _shop()["products"]
    if low_stock_below:
        items = [p for p in items if p["stock"] < low_stock_below]
    return {"products": items, "featured": _featured(ctx), "currency": ctx.settings.get("currency", "USD")}


def orders(_ctx: Context, day: str = "today") -> dict[str, Any]:
    # The e-mail addresses are in the data, but the plugin marks "email" personal: no agent ever sees them.
    return {"orders": [o for o in _shop()["orders"] if o["day"] == day]}


# --- the action: what agents may propose ------------------------------------------------------------------------
def check_feature(ctx: Context, payload: dict[str, Any]) -> list[str]:
    product = next((p for p in _shop()["products"] if p["id"] == payload["product_id"]), None)
    if product is None:
        return [f"No product {payload['product_id']}."]
    if product["stock"] == 0:
        return [f"{product['name']} is sold out; featuring it would disappoint people."]
    if payload["product_id"] in _featured(ctx):
        return [f"{product['name']} is on the front page already."]
    return []


def feature(ctx: Context, payload: dict[str, Any], approval_id: int) -> dict[str, Any]:
    featured = _featured(ctx)
    if payload["product_id"] not in featured:
        featured.append(payload["product_id"])
        (ctx.data_dir / "featured.json").write_text(json.dumps(featured))
    ctx.emit("featured", {"product_id": payload["product_id"], "approval_id": approval_id})
    return {"featured": featured}


# --- numbers ----------------------------------------------------------------------------------------------------
def orders_today(_ctx: Context) -> dict[str, float]:
    per_country: dict[str, float] = {}
    for o in _shop()["orders"]:
        if o["day"] == "today":
            per_country[o["country"]] = per_country.get(o["country"], 0) + 1
    return per_country


def sold_out(_ctx: Context) -> float:
    return float(sum(1 for p in _shop()["products"] if p["stock"] == 0))


plugin = Plugin(
    key="hello_shop",
    name="Hello Shop",
    version="1.0.0",
    api="1.0",
    description="A pretend shop to show every part of a plugin.",
    settings=(Setting("currency", "Currency shown to agents", hint="For example USD or EUR.", required=True),),
    personal=("email",),
    tools=(
        Tool("products", "The shop's products with price and stock, and which are on the front page.", products,
             params={"low_stock_below": Param("integer", "Only products with less stock than this.")}, view=True),
        Tool("orders", "The orders of one day (today or yesterday): product, quantity, country.", orders,
             params={"day": Param("string", "today or yesterday")}, view=True),
    ),
    actions=(
        Action("feature", "Put a product on the front page", feature, ceiling="L2", check=check_feature, recheck=check_feature,
               target="product_id",
               fields={"product_id": Param("string", "The product's id from products.", required=True),
                       "why": Param("string", "Why this one, with the numbers.")}),
    ),
    metrics=(
        Metric("orders", "Orders", orders_today, hint="Orders per day, by country."),
        Metric("sold_out", "Products sold out", sold_out, good="down", agg="last"),
    ),
    templates=(
        Template("merchandiser", "Merchandiser", "Shop",
                 "Keeps the shop's front page on products that sell and are in stock, and warns about stock running out.",
                 "Read products and today's orders. Propose (feature) at most one product per run for the front page: in stock, "
                 "selling, and not featured yet; say why with the numbers. Name products whose stock is below five. "
                 "Facts only from your tools.",
                 tools=("products", "orders"), actions=("feature",), kpi="orders"),
    ),
)
