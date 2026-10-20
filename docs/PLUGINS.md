# Writing a plugin

A plugin connects a project — an app, a shop, a website — to Control Tower. It gives the owner's agents something to
read, something to propose, numbers to judge them by, and starter agents that suit the project. Interface version
**1.0** (`app/plugin_api.py`); a Control Tower of the same major version runs a plugin unchanged.

There are two ways to write one. They give exactly the same things:

| | Python plugin | HTTP connection |
|---|---|---|
| For | a project you can reach from Python (its database, its API) | a project in any language, or on another server |
| Lives | `plugins/<key>/plugin.py` on the Control Tower server | in your project, as a few HTTPS endpoints |
| Added by | copying the folder and restarting | Admin → Plugins → **Connect a project** |

Whichever you choose, the owner switches the plugin **on** in Admin → Plugins. Until then nothing of it is offered and
every call to it is refused.

## What a plugin can give

- **Tools** — read-only questions agents may ask the project (`customers`, `orders`, `failed_payments`). The owner ticks
  per agent which tools it gets. A tool returns a JSON object. Keep answers small (at most 60,000 characters reach the
  model; longer ones are cut): give tools filters and limits.
- **Actions** — things agents may *propose* to do in the project (`tag_customer`, `send_coupon`, `pause_account`). The
  agent fills the action's fields; Control Tower checks their types, then your `check`; the owner clears it; then your
  `execute` runs **once**, with the approval id. Nothing in your project changes before that.
  - `ceiling`: `L3` = always the owner, never alone — use it for money, deleting, anything about a person's account.
    `L2` = the owner may later let it run alone within a daily limit. When unsure: `L3` (the default).
  - `target`: the field naming what the proposal is about (`customer_id`). A second proposal for the same value is
    refused while the first waits. Empty: one waiting proposal of this kind per agent.
  - `check`: problems that make the proposal pointless (the customer does not exist, it is done already). The agent
    hears them at once.
  - `recheck`: asked again every minute while the proposal waits; problems withdraw it (it became a duplicate).
- **Metrics** — the project's numbers, read every quarter hour into Insights as `<plugin>_<metric>`. Return today's
  value, or one value per dimension (`{"pro": 12, "free": 40}`). `agg` says how days add up over a period: `sum` for
  things that happen (sign-ups), `last` for totals (customers now).
- **Templates** — starter agents for Team → New agent. Name your own tools, actions and metrics by their short key;
  the owner can change everything before making the agent.
- **Settings** (Python only) — what the owner fills in on Admin → Plugins: an address, a key. `secret=True` values are
  stored encrypted and never shown again. `long=True` gives a box of several lines (a JSON list, a text).
- **Events** — your project tells the team something happened; agents subscribed to `<plugin>.<type>` wake up.
- **Personal fields** — names of fields that hold personal data (`email`, `phone`, `name`). Control Tower replaces
  them with "(personal — hidden)" in every tool answer, action result and event, at any depth, before an agent or a
  view sees them. List every such field. Better still, do not return personal data at all.

Names: the plugin key is 2–24 characters, lowercase letters, digits and `_`, starting with a letter. Tools appear as
`<plugin>_<tool>`, actions as `<plugin>.<action>`, metrics as `<plugin>_<metric>`, templates as `<plugin>.<template>`,
events as `<plugin>.<type>`. A plugin whose names are taken is not loaded (the reason is shown on Admin → Plugins).

## A Python plugin

```
plugins/
  shop/
    plugin.py        # defines plugin = Plugin(...)
    client.py        # any other files; import them relatively: from .client import Shop
```

The folder name is the plugin's key. A complete example to copy: `examples/plugins/hello_shop/plugin.py`. Two real
ones ship with every install, read-only: `plugins/stripe` and `plugins/database`.

A Python plugin runs inside Control Tower with all of its rights (its database, its secrets, its files). Install only
plugin code you wrote or trust, as you would any code on your server. A project you trust less belongs behind the HTTP
connector: it then sees only what Control Tower sends it.

```python
from app.plugin_api import Action, Context, Metric, Param, Plugin, Setting, Template, Tool

def customers(ctx: Context, plan: str = "") -> dict:
    rows = Shop(ctx.settings["api_url"], ctx.settings["api_key"]).customers(plan=plan, limit=50)
    return {"customers": rows}

def tag(ctx: Context, payload: dict, approval_id: int) -> dict:
    Shop(...).tag(payload["customer_id"], payload["tag"], idempotency_key=f"ct-{approval_id}")
    ctx.emit("tagged", {"customer_id": payload["customer_id"]})
    return {"tagged": payload["customer_id"]}

plugin = Plugin(
    key="shop", name="My Shop", version="1.0.0", api="1.0",
    settings=(Setting("api_url", "Shop API address", required=True), Setting("api_key", "Shop API key", secret=True, required=True)),
    personal=("email", "phone", "name"),
    tools=(Tool("customers", "Customers, optionally of one plan.", customers, params={"plan": Param("string", "free or pro")}, view=True),),
    actions=(Action("tag", "Tag a customer", tag, ceiling="L3", target="customer_id",
                    fields={"customer_id": Param("integer", required=True), "tag": Param("string", required=True)}),),
    metrics=(Metric("customers", "Customers", lambda ctx: Shop(...).count(), agg="last"),),
)
```

What your functions receive (`Context`): `ctx.plugin` (the key), `ctx.settings` (the owner's settings, secrets
decrypted), `ctx.agent` (the agent's key, or "" for metrics and views), `ctx.data_dir` (a folder only this plugin
writes to) and `ctx.emit(type, payload)`.

- Tools: `run(ctx, **params) -> dict`. Params arrive with their declared types; unknown ones are dropped.
- Actions: `execute(ctx, payload, approval_id) -> dict`, `check(ctx, payload) -> list[str]`, `recheck(ctx, payload) -> list[str]`.
- Metrics: `read(ctx) -> float | dict[str, float]`.

**Parts that follow the owner's settings.** When what a plugin offers depends on its settings — the database plugin
turns each query the owner writes into a tool — give the `Plugin` a `build(settings) -> Plugin` function. Control
Tower calls it with the settings (secrets decrypted) when the plugin loads and whenever its settings change, and uses
the Plugin it returns (same key). A `ValueError` from it keeps the fixed parts and shows the message on Admin →
Plugins. See `plugins/database/plugin.py`.

An exception in a tool becomes "failed — do not guess" for the agent; in `execute` the proposal is marked failed with
the reason; in `check` the proposal is refused (try again later); in `recheck` it is ignored (an unreachable project
never withdraws the owner's proposals). A plugin whose code fails to import is shown on Admin → Plugins and left out;
the rest still load. Extra Python packages a plugin needs must be installed in Control Tower's environment.

## An HTTP connection

Your project serves these under one base address (for example `https://shop.example.com/control-tower`). All bodies
are JSON. Every request from Control Tower is **signed**; refuse any request whose signature does not verify.

| Request | Body | Answer |
|---|---|---|
| `GET /manifest` | — | the manifest (below) |
| `POST /tools/<tool>` | `{"args": {...}, "agent": "<agent key>"}` | any JSON object |
| `POST /actions/<action>/check` | `{"payload": {...}}` | `{"problems": ["…"]}` (empty: fine) |
| `POST /actions/<action>/recheck` | `{"payload": {...}}` | `{"problems": [...]}` |
| `POST /actions/<action>/execute` | `{"payload": {...}, "approval_id": 12}` | any JSON object (the result) |
| `GET /metrics?since=<ISO time>` | — | `{"values": {"<metric>": 41, "<metric>": {"<dimension>": 3}}}` |

`since` is the start of the owner's day in their time zone (UTC, e.g. `2026-10-20T22:00:00Z`): a number that counts
what happens (sign-ups) counts from then; a total (users) ignores it. The signature covers the path without the query.
An answer with status 400 or more counts as failed; put the reason in `{"error": "…"}`. Answer within 15 seconds and
under 2 MB. Control Tower calls `execute` once per cleared proposal; still, refuse an `approval_id` you have carried
out already (answer 409) — that makes a repeat harmless.

The manifest:

```json
{
  "api": "1.0", "key": "shop", "name": "My Shop", "version": "1.0.0", "description": "…",
  "personal": ["email", "phone", "name"],
  "tools": [{"key": "customers", "description": "Customers, optionally of one plan.",
             "params": {"plan": {"type": "string", "description": "free or pro"}}, "view": true}],
  "actions": [{"key": "tag", "label": "Tag a customer", "ceiling": "L3", "target": "customer_id", "check": true, "recheck": false,
               "fields": {"customer_id": {"type": "integer", "required": true}, "tag": {"type": "string", "required": true}}}],
  "metrics": [{"key": "customers", "label": "Customers", "unit": "count", "good": "up", "agg": "last"}],
  "templates": [{"key": "account_manager", "name": "Account Manager", "department": "Sales",
                 "job": "Looks after the paying customers and proposes follow-ups.", "guide": "…",
                 "tools": ["customers"], "actions": ["tag"], "kpi": "customers"}]
}
```

Types are `string`, `integer`, `number`, `boolean`, `array` (with `items`) and `object`. `key` must be the key the
owner gave the connection. `check` / `recheck` say whether your project answers those addresses.

To connect: the owner presses **Connect a project**, gives the key and your base address, and gets the **shared
secret** once. Put it in your project's settings (an environment variable). Then the owner presses **Fetch**; press it
again whenever your manifest changes.

## Signing (both ways)

Header `X-Control-Tower-Signature: t=<unix seconds>,v1=<hex>` where

```
hex = HMAC-SHA256(secret, "<t>.<path>." + raw body)
```

`<path>` is the request's path without host or query (`/control-tower/tools/customers`), the body is the exact bytes
sent (empty for GET). Refuse a request whose `t` is more than 300 seconds from your clock. Python:

```python
import hashlib, hmac, time

def verify(secret: str, path: str, body: bytes, header: str) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - t) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{t}.{path}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

Node:

```js
import { createHmac, timingSafeEqual } from "node:crypto";

export function verify(secret, path, body /* Buffer */, header) {
  const parts = Object.fromEntries((header || "").split(",").map((p) => p.split("=", 2)));
  const t = Number(parts.t);
  if (!t || Math.abs(Date.now() / 1000 - t) > 300 || !parts.v1) return false;
  const expected = createHmac("sha256", secret).update(`${t}.${path}.`).update(body).digest("hex");
  return expected.length === parts.v1.length && timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}
```

Sign your own requests to Control Tower the same way.

## Events: your project tells the team

```
POST <control tower>/plugin-api/v1/<plugin>/events
X-Control-Tower-Signature: t=…,v1=…        (path /plugin-api/v1/<plugin>/events)
{"events": [{"type": "signup", "payload": {"plan": "pro"}}]}
```

At most 50 events and 64 KB per request. Types are lowercase letters, digits, `_` and dots. Each becomes the event
`<plugin>.<type>` (here `shop.signup`) for agents whose design is woken by it. Answers: 202 received, 401 refused (bad
or old signature, or the plugin is off), 422 the body is not right. This address is reachable from the internet; the
signature is the only credential, so keep the secret out of code and logs. Both kinds of plugin can use it; a Python
plugin can also call `ctx.emit` directly.

## What a plugin can never do

These are the core's rules and no plugin changes them: an action runs only after clearance (or within the limit the
owner set for an L2 action), and only once; personal fields never reach an agent; a plugin's tools reach only the
agents the owner gives them to; everything is in the admin log; switching the plugin off stops all of it at once.
