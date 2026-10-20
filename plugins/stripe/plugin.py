"""Stripe, read-only: the business's subscriptions, payments, failed payments and revenue for the agents, and its
numbers in Insights (monthly recurring revenue, active subscriptions, failed payments, revenue per day).

It never changes anything in Stripe: no refunds, no cancellations, no emails. Use a *restricted* key with read access
only (Stripe → Developers → API keys → Create restricted key: Read on Customers, Subscriptions, Charges, Prices,
Balance transactions). Customers' e-mail addresses, names, phones and addresses never reach an agent.

Amounts are shown in the main currency's units (euros, dollars …); other currencies are listed apart, never added in.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.plugin_api import Context, Metric, Param, Plugin, Setting, Template, Tool

API = "https://api.stripe.com/v1"
# Tests replace the transport; None = the network.
TRANSPORT: httpx.BaseTransport | None = None
# Safety rails: pages of 100 read for one answer, and the rows an agent gets.
MAX_PAGES = 20
ROWS_SHOWN = 50
# Currencies without minor units (Stripe's list): amounts are already whole units.
ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}


def _amount(cents: int, currency: str) -> float:
    return float(cents) if currency.lower() in ZERO_DECIMAL else round(cents / 100, 2)


def _list(ctx: Context, path: str, params: dict[str, Any], *, pages: int = MAX_PAGES) -> list[dict[str, Any]]:
    key = str(ctx.settings.get("api_key") or "")
    if not key:
        raise ValueError("The Stripe key is not set (Admin → Plugins → Stripe).")
    out: list[dict[str, Any]] = []
    query = {**params, "limit": 100}
    with httpx.Client(timeout=30, transport=TRANSPORT, auth=(key, "")) as client:
        for _ in range(pages):
            response = client.get(f"{API}/{path}", params=query)
            if response.status_code >= 400:
                message = (response.json().get("error") or {}).get("message", "") if response.content else ""
                raise RuntimeError(f"Stripe answered {response.status_code}: {message[:200]}")
            data = response.json()
            out.extend(data.get("data") or [])
            if not data.get("has_more") or not out:
                break
            query["starting_after"] = out[-1]["id"]
    return out


def _since(days: int) -> int:
    return int((datetime.now(UTC) - timedelta(days=max(1, min(days, 366)))).timestamp())


def _today() -> int:
    from app.core.clock import zone

    now = datetime.now(zone())
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _monthly(item: dict[str, Any]) -> tuple[str, float]:
    """A subscription item's amount per month, in its currency."""
    price = item.get("price") or {}
    recurring = price.get("recurring") or {}
    currency = str(price.get("currency") or "")
    amount = _amount(int(price.get("unit_amount") or 0), currency) * int(item.get("quantity") or 1)
    interval, count = recurring.get("interval", "month"), int(recurring.get("interval_count") or 1)
    per_month = {"day": 30.4375, "week": 4.348, "month": 1.0, "year": 1 / 12}.get(interval, 1.0) / count
    return currency, amount * per_month


# --- tools --------------------------------------------------------------------------------------------------------
def subscriptions(ctx: Context, status: str = "active", limit: int = 20) -> dict[str, Any]:
    status = status if status in ("active", "past_due", "trialing", "canceled", "unpaid", "all") else "active"
    rows = _list(ctx, "subscriptions", {"status": status, "expand[]": "data.items.data.price"})
    shown = []
    for sub in rows[: max(1, min(limit, ROWS_SHOWN))]:
        items = (sub.get("items") or {}).get("data") or []
        monthly = [_monthly(i) for i in items]
        shown.append({"id": sub["id"], "customer": sub.get("customer"), "status": sub.get("status"),
                      "monthly": {c: round(sum(a for cc, a in monthly if cc == c), 2) for c, _ in monthly},
                      "plans": [((i.get("price") or {}).get("nickname") or (i.get("price") or {}).get("id")) for i in items],
                      "started": datetime.fromtimestamp(int(sub.get("start_date") or 0), UTC).date().isoformat(),
                      "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
                      "current_period_end": datetime.fromtimestamp(int(sub.get("current_period_end") or 0), UTC).date().isoformat()})
    return {"status": status, "total": len(rows), "shown": shown}


def payments(ctx: Context, days: int = 7, status: str = "all") -> dict[str, Any]:
    rows = _list(ctx, "charges", {"created[gte]": _since(days)})
    if status in ("succeeded", "failed"):
        rows = [r for r in rows if r.get("status") == status]
    shown = [{"id": r["id"], "customer": r.get("customer"), "amount": _amount(int(r.get("amount") or 0), str(r.get("currency"))),
              "currency": r.get("currency"), "status": r.get("status"), "refunded": _amount(int(r.get("amount_refunded") or 0), str(r.get("currency"))),
              "failure": r.get("failure_message") or "", "created": datetime.fromtimestamp(int(r.get("created") or 0), UTC).isoformat(timespec="minutes"),
              "billing_details": r.get("billing_details"), "receipt_email": r.get("receipt_email")} for r in rows[:ROWS_SHOWN]]
    return {"days": days, "count": len(rows), "failed": sum(1 for r in rows if r.get("status") == "failed"), "shown": shown}


def revenue(ctx: Context, days: int = 30) -> dict[str, Any]:
    rows = _list(ctx, "charges", {"created[gte]": _since(days)})
    per: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("status") != "succeeded":
            continue
        currency = str(r.get("currency"))
        bucket = per.setdefault(currency, {"paid": 0.0, "refunded": 0.0, "payments": 0})
        bucket["paid"] += _amount(int(r.get("amount") or 0), currency)
        bucket["refunded"] += _amount(int(r.get("amount_refunded") or 0), currency)
        bucket["payments"] += 1
    return {"days": days, "by_currency": {c: {**v, "net": round(v["paid"] - v["refunded"], 2)} for c, v in per.items()}}


# --- numbers ------------------------------------------------------------------------------------------------------
def _main(ctx: Context) -> str:
    return str(ctx.settings.get("currency") or "usd").lower()


def mrr(ctx: Context) -> float:
    currency = _main(ctx)
    total = 0.0
    for sub in _list(ctx, "subscriptions", {"status": "active", "expand[]": "data.items.data.price"}):
        for item in (sub.get("items") or {}).get("data") or []:
            c, amount = _monthly(item)
            total += amount if c == currency else 0.0
    return round(total, 2)


def active_subscriptions(ctx: Context) -> float:
    return float(len(_list(ctx, "subscriptions", {"status": "active"})))


def failed_payments(ctx: Context) -> float:
    return float(sum(1 for r in _list(ctx, "charges", {"created[gte]": _today()}) if r.get("status") == "failed"))


def revenue_today(ctx: Context) -> float:
    currency = _main(ctx)
    return round(sum(_amount(int(r.get("amount") or 0) - int(r.get("amount_refunded") or 0), currency)
                     for r in _list(ctx, "charges", {"created[gte]": _today()}) if r.get("status") == "succeeded" and r.get("currency") == currency), 2)


def build(values: Mapping[str, Any]) -> Plugin:
    """The labels name the owner's main currency."""
    currency = str(values.get("currency") or "usd").upper()
    return _plugin(currency)


def _plugin(currency: str) -> Plugin:
    return Plugin(
        key="stripe",
        name="Stripe",
        version="1.0.0",
        api="1.0",
        description="Your Stripe account, read-only: subscriptions, payments, failed payments and revenue. It never changes anything in Stripe.",
        settings=(
            Setting("api_key", "Restricted key (read-only)", hint="Stripe → Developers → API keys → Create restricted key, with Read on Customers, "
                    "Subscriptions, Charges and Prices. It starts rk_live_ (or rk_test_ to try).", secret=True, required=True),
            Setting("currency", "Main currency", hint="Three letters, e.g. usd or eur. Numbers in Insights count this currency.", required=True),
        ),
        personal=("email", "receipt_email", "name", "phone", "address", "billing_details", "shipping"),
        tools=(
            Tool("subscriptions", "Subscriptions by status (active, past_due, trialing, canceled, unpaid, all): plans, amount per month, "
                 "started, renews or ends.", subscriptions,
                 params={"status": Param("string", "active, past_due, trialing, canceled, unpaid or all"), "limit": Param("integer", "At most 50.")}, view=True),
            Tool("payments", "Payments of the last days: amount, status, why a payment failed, refunds.", payments,
                 params={"days": Param("integer", "1–366"), "status": Param("string", "all, succeeded or failed")}, view=True),
            Tool("revenue", "Money received over the last days, per currency: paid, refunded, net, number of payments.", revenue,
                 params={"days": Param("integer", "1–366")}, view=True),
        ),
        metrics=(
            Metric("mrr", f"Monthly recurring revenue ({currency})", mrr, agg="last", hint="Active subscriptions, as an amount per month."),
            Metric("active_subscriptions", "Active subscriptions", active_subscriptions, agg="last"),
            Metric("failed_payments", "Failed payments", failed_payments, good="down", hint="Payments that failed, per day."),
            Metric("revenue", f"Revenue ({currency})", revenue_today, hint="Payments received per day, minus refunds."),
        ),
        templates=(
            Template("revenue_watcher", "Revenue Watcher", "Finance",
                     "Watches the money coming in: failed payments, subscriptions about to end or past due, and how revenue moves — and "
                     "tells the owner what needs attention.",
                     "Read revenue for the last 30 days and payments of the last 7 days, then subscriptions past_due and those that end at "
                     "the period's end. Report, most urgent first: failed payments (how many, how much, the reasons), subscriptions past due, "
                     "cancellations coming, and how revenue compares with the 30 days before (ask revenue for 60 days). Name customers only by "
                     "their Stripe id. You never change anything in Stripe. Facts only from your tools.",
                     tools=("subscriptions", "payments", "revenue"), kpi="mrr", max_rounds=10),
        ),
        build=build,
    )


plugin = _plugin("USD")
