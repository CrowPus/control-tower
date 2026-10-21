# Connect a project to Control Tower — the brief

**For the coding agent or developer working in the project's own code** (Claude Code, Cursor, Copilot, Lovable,
Codex …). The owner may not be a developer: explain what you do in plain words, ask before choosing what to expose,
and finish only when the connection check passes. This file is complete on its own; the full reference is
[PLUGINS.md](PLUGINS.md) in the Control Tower repository.

## What you are building

Control Tower is a self-hosted office for the owner's AI agents. It calls **the project** over HTTPS to learn what
agents may read and do. You add a small set of routes to the project, under one base path (usually
`/control-tower`), that:

1. describe what the project offers (`GET /manifest`),
2. answer read-only questions (`POST /tools/<tool>`),
3. report numbers (`GET /metrics`),
4. optionally carry out actions the owner cleared (`POST /actions/<action>/execute`, with `check`),
5. optionally tell Control Tower when something happens (events, sent by the project).

Every request in both directions is **signed** with a shared secret. Nothing else in the project changes.

## Ground rules

1. **Read-only first.** Build tools and metrics. Build an action only when the owner asks for that action by name.
   Never build an action that spends money, deletes, or changes someone's account without the owner saying so; give
   any such action `"ceiling": "L3"` (always the owner).
2. **Ask before you choose.** Look at the project, then propose a short list to the owner in plain words — "Agents
   could see: new sign-ups per day, the newest customers, failed payments. OK?" — and build what they agree to.
3. **Personal data:** prefer ids and counts. Any field that holds personal data (email, name, phone, address,
   ip …) must be listed in the manifest's `personal` list; Control Tower hides those fields from agents. Events
   carry ids only, never personal data.
4. **The secret** comes from the environment variable `CONTROL_TOWER_SECRET`. Never write it in code, tests, logs,
   commits or chat. Without it set, the routes must not exist (or answer 404).
5. **Use the project's own way** of reading data (its models, repositories, ORM) and its own permission checks for
   actions. Do not open a second database connection or bypass access rules.
6. **Never trust input**: tool arguments and action payloads come from an AI agent. Validate types, clamp limits
   (at most ~100 rows per answer), and run only the queries you wrote — never SQL or code taken from a request.
7. **Do not wait on Control Tower**: sending an event must never slow down or break the project's own work.

## The plan

1. Read the project: its framework, how it reads data, how it is configured (env vars), how it is tested.
2. Propose the list (tools, numbers, maybe events) to the owner; wait for an OK.
3. Add `CONTROL_TOWER_SECRET`, `CONTROL_TOWER_URL` and `CONTROL_TOWER_KEY` to the project's configuration and its
   example env file, with one-line comments.
4. Add the routes (the reference code below adapts to most frameworks), registered only when the secret is set,
   outside any session/login middleware (the signature is the credential).
5. Write tests in the project's own style: unsigned → 401, signed manifest → 200, each tool → JSON object.
6. Run the project locally and run the connection check (below). Fix until it passes.
7. Tell the owner, in plain words: what agents can now see and do, which settings to set where (the secret from
   Control Tower → `CONTROL_TOWER_SECRET`), to deploy as usual, then press **Check the connection** in Control
   Tower → Plugins.

## The protocol

All bodies are JSON. The base path below is `<base>` (e.g. `https://shop.example.com/control-tower`).

| Request | Body | Answer |
| --- | --- | --- |
| `GET <base>/manifest` | — | the manifest |
| `POST <base>/tools/<tool>` | `{"args": {...}, "agent": "<agent key>"}` | any JSON **object** |
| `GET <base>/metrics?since=<ISO time>` | — | `{"values": {"<metric>": 12, "<metric>": {"<dimension>": 3}}}` |
| `POST <base>/actions/<action>/check` | `{"payload": {...}}` | `{"problems": ["…"]}` — empty list: fine |
| `POST <base>/actions/<action>/recheck` | `{"payload": {...}}` | `{"problems": [...]}` (problems withdraw a waiting proposal) |
| `POST <base>/actions/<action>/execute` | `{"payload": {...}, "approval_id": 12}` | any JSON object (the result) |

- Answer status 400+ for failure, with `{"error": "a short reason"}`; 401 with nothing more for a bad signature;
  404 for an unknown tool or action; 409 when `execute` was carried out already for that `approval_id`.
- Answer within 15 seconds and under 2 MB.
- `since` (metrics) is the start of the owner's day: numbers that count events ("sign-ups") count from then;
  totals ("customers") ignore it.

### The manifest

```json
{
  "api": "1.0",
  "key": "shop",
  "name": "My Shop",
  "version": "1.0.0",
  "description": "One sentence: what this project is.",
  "personal": ["email", "name", "phone"],
  "tools": [
    {"key": "overview", "description": "The shop in numbers over the last days: orders, revenue, new customers.",
     "params": {"days": {"type": "integer", "description": "How many days back (1–90)."}}, "view": true},
    {"key": "newest_customers", "description": "Customers who signed up in the last days.",
     "params": {"days": {"type": "integer"}, "limit": {"type": "integer"}}, "view": true}
  ],
  "actions": [],
  "metrics": [
    {"key": "orders", "label": "Orders", "agg": "sum"},
    {"key": "customers", "label": "Customers", "agg": "last"}
  ],
  "templates": [
    {"key": "sales_watch", "name": "Sales Watch", "department": "Sales",
     "job": "Watches orders and new customers and tells the owner what moves and why.",
     "guide": "Read overview for 7 and 28 days, compare, name what changed. Facts only from your tools.",
     "tools": ["overview", "newest_customers"], "kpi": "orders"}
  ]
}
```

- Keys: lowercase letters, digits and `_`, starting with a letter; `key` (the plugin key) is the one the owner
  chose in Control Tower — read it from `CONTROL_TOWER_KEY`.
- Param and field types: `string`, `integer`, `number`, `boolean`, `array` (with `"items"`), `object`; mark required
  ones with `"required": true`.
- `view: true` lets the owner open the tool as a table (only for tools without required params).
- Metric `agg`: `sum` (per-day counts), `last` (totals), `avg`, `max`; `good`: `up`, `down` or `neutral`;
  `unit`: `count`, `usd`, `ms`, `hours`, `percent`.
- An action: `{"key": "…", "label": "…", "ceiling": "L3", "fields": {…}, "target": "<field naming what it is about>",
  "check": true, "recheck": false}`. `check: true` means you serve `/check`.
- Templates name the project's own tools, actions and metrics by their short keys. Write the `job` in one or two
  sentences and the `guide` as concrete steps; end the guide with "Facts only from your tools."

### Signing

Header `X-Control-Tower-Signature: t=<unix seconds>,v1=<hex>`, where

```
hex = HMAC-SHA256(secret, "<t>.<path>." + raw body bytes)
```

`<path>` is the request path without host or query, exactly as Control Tower called it (`/control-tower/manifest`).
The body is the exact bytes (empty for GET) — so read the **raw** body before any JSON parsing. Refuse when `t` is
more than 300 seconds from now. Compare in constant time.

**Behind a proxy that changes the path** (e.g. strips `/api`), verify with the path as Control Tower called it, not as
your server sees it — or register the routes so both are the same.

### Events (optional)

```
POST <CONTROL_TOWER_URL>/plugin-api/v1/<key>/events
X-Control-Tower-Signature: …   (signed the same way, path /plugin-api/v1/<key>/events)
{"events": [{"type": "signup", "payload": {"user_id": "u_123"}}]}
```

Types: lowercase letters, digits, `_` and dots; agents see them as `<key>.<type>`. At most 50 events and 64 KB per
request. Send after the project's own work is done, in the background, with a short timeout, ignoring failures.

## Reference code

Adapt to the project's framework; keep the behaviour.

### Node.js (Express)

```js
import crypto from "node:crypto";
import express from "express";

const SECRET = process.env.CONTROL_TOWER_SECRET ?? "";
const KEY = process.env.CONTROL_TOWER_KEY ?? "myproject";
const MAX_ROWS = 100;

function verify(req) {
  const header = req.get("x-control-tower-signature") ?? "";
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=", 2)));
  const t = Number(parts.t);
  if (!SECRET || !Number.isInteger(t) || !parts.v1 || Math.abs(Date.now() / 1000 - t) > 300) return false;
  const body = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
  const path = req.originalUrl.split("?")[0];
  const expected = crypto.createHmac("sha256", SECRET).update(`${t}.${path}.`).update(body).digest("hex");
  return expected.length === parts.v1.length && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}

const clamp = (v, fallback, min, max) => Math.min(max, Math.max(min, Number.isInteger(v) ? v : fallback));

// What agents may read: each returns a plain object. Use the project's own data access here.
const TOOLS = {
  async overview({ days }) {
    const since = new Date(Date.now() - clamp(days, 7, 1, 90) * 86_400_000);
    return { days: clamp(days, 7, 1, 90), orders: await db.orders.countSince(since), customers: await db.customers.count() };
  },
  async newest_customers({ days, limit }) {
    const since = new Date(Date.now() - clamp(days, 7, 1, 90) * 86_400_000);
    const rows = await db.customers.createdSince(since, clamp(limit, 20, 1, MAX_ROWS));
    return { customers: rows.map((c) => ({ id: c.id, email: c.email, created: c.createdAt })) };
  },
};

const MANIFEST = { api: "1.0", key: KEY, name: "My Shop", version: "1.0.0", personal: ["email"], tools: [/* … */], actions: [], metrics: [/* … */], templates: [/* … */] };

export function controlTower() {
  const router = express.Router();
  router.use(express.raw({ type: "*/*", limit: "256kb" })); // the signature covers the exact bytes
  router.use((req, res, next) => (verify(req) ? next() : res.status(401).json({ error: "refused" })));
  const json = (req) => (Buffer.isBuffer(req.body) && req.body.length ? JSON.parse(req.body.toString("utf8")) : {});

  router.get("/manifest", (_req, res) => res.json(MANIFEST));
  router.get("/metrics", async (req, res) => {
    const since = new Date(typeof req.query.since === "string" ? req.query.since : new Date().setUTCHours(0, 0, 0, 0));
    res.json({ values: { orders: await db.orders.countSince(since), customers: await db.customers.count() } });
  });
  router.post("/tools/:tool", async (req, res) => {
    const tool = TOOLS[req.params.tool];
    if (!tool) return res.status(404).json({ error: "no such tool" });
    try {
      res.json(await tool(json(req).args ?? {}));
    } catch (error) {
      res.status(500).json({ error: String(error?.message ?? error).slice(0, 300) });
    }
  });
  return router;
}

// In the app's setup, before any login middleware:
// if (process.env.CONTROL_TOWER_SECRET) app.use("/control-tower", controlTower());

export function sendEvent(type, payload) {
  const url = process.env.CONTROL_TOWER_URL;
  if (!SECRET || !url) return;
  const path = `/plugin-api/v1/${KEY}/events`;
  const body = JSON.stringify({ events: [{ type, payload }] });
  const t = Math.floor(Date.now() / 1000);
  const v1 = crypto.createHmac("sha256", SECRET).update(`${t}.${path}.`).update(body).digest("hex");
  fetch(url.replace(/\/$/, "") + path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-control-tower-signature": `t=${t},v1=${v1}` },
    body,
    signal: AbortSignal.timeout(5000),
  }).catch(() => {}); // never let Control Tower slow the project down
}
```

### Python (FastAPI)

```python
import hashlib, hmac, json, os, time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

SECRET = os.environ.get("CONTROL_TOWER_SECRET", "")
KEY = os.environ.get("CONTROL_TOWER_KEY", "myproject")
router = APIRouter(prefix="/control-tower")
MANIFEST = {"api": "1.0", "key": KEY, "name": "My Shop", "version": "1.0.0", "personal": ["email"], "tools": [], "actions": [], "metrics": [], "templates": []}  # fill in as above


async def signed(request: Request) -> dict:
    body = await request.body()
    try:
        parts = dict(p.split("=", 1) for p in request.headers.get("x-control-tower-signature", "").split(","))
        t = int(parts["t"])
    except (KeyError, ValueError):
        raise HTTPException(401, "refused") from None
    expected = hmac.new(SECRET.encode(), f"{t}.{request.url.path}.".encode() + body, hashlib.sha256).hexdigest()
    if not SECRET or abs(time.time() - t) > 300 or not hmac.compare_digest(expected, parts.get("v1", "")):
        raise HTTPException(401, "refused")
    return json.loads(body) if body else {}


@router.get("/manifest")
async def manifest(request: Request) -> dict:
    await signed(request)
    return MANIFEST


@router.post("/tools/{tool}")
async def tool(tool: str, request: Request) -> dict:
    args = (await signed(request)).get("args") or {}
    if tool == "overview":
        days = max(1, min(int(args.get("days") or 7), 90))
        since = datetime.now(UTC) - timedelta(days=days)
        return {"days": days, "orders": count_orders_since(since)}   # the project's own data access
    raise HTTPException(404, "no such tool")


@router.get("/metrics")
async def metrics(request: Request, since: str | None = None) -> dict:
    await signed(request)
    start = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"values": {"orders": count_orders_since(start), "customers": count_customers()}}

# In the app: if SECRET: app.include_router(router)
```

## The connection check

Before telling the owner it is done, run the check against the running project. It needs only Python 3:

```bash
curl -fsSL https://raw.githubusercontent.com/CrowPus/control-tower/main/tools/check_connection.py -o check_connection.py
CONTROL_TOWER_SECRET=<a test secret the project is running with> python3 check_connection.py http://localhost:3000/control-tower
```

(Or download it from Control Tower → Plugins → Connect your project.) It checks that unsigned and wrongly signed
requests are refused, the manifest is valid, every metric answers with numbers, and every tool without required
parameters answers with a JSON object. Every line must say PASS. Delete the downloaded script afterwards; it is not
part of the project.

## Hand-over to the owner

Say, in plain words:

- what agents can now see (each tool in one line) and which numbers appear in Insights,
- which actions exist (if any) and that each waits for their clearance,
- the settings to set where the project runs: `CONTROL_TOWER_SECRET` = the shared secret Control Tower showed,
  `CONTROL_TOWER_URL` = Control Tower's address (for events), `CONTROL_TOWER_KEY` = the key they chose,
- to deploy the project as usual, then in Control Tower → Plugins press **Check the connection**, then switch it on.
