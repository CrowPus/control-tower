"""The worker's part of Mail: every account mirrored each minute, one account when
someone presses refresh or changed something, and each mail of the outbox when its time has come."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ...db import transaction
from ...models import AgentTask, MailAccount, MailOutbox
from ...workers import queue as q
from ...workers.worker import task
from . import outgoing, sync


@task("mail.sync_all", queue="maintenance")
def mail_sync_all(_job: AgentTask) -> dict[str, Any]:
    with transaction(write=True) as session:
        ids = list(session.execute(select(MailAccount.id).where(MailAccount.status != "paused")).scalars())
        for account_id in ids:
            q.enqueue(session, "mail.sync", {"account_id": account_id}, queue="maintenance", max_attempts=1, dedupe_key=f"mail.sync:{account_id}")
        # A mail whose task was lost (a restart at the wrong moment) is sent now.
        late = list(session.execute(select(MailOutbox.id).where(MailOutbox.status == "queued", MailOutbox.send_at < datetime.now(UTC))).scalars())
        for outbox_id in late:
            q.enqueue(session, "mail.send", {"outbox_id": outbox_id}, queue="notifications", priority=1, max_attempts=1, dedupe_key=f"mail.send:{outbox_id}")
    return {"accounts": len(ids), "late": len(late)}


@task("mail.sync", queue="maintenance")
def mail_sync(job: AgentTask) -> dict[str, Any]:
    with transaction(write=True) as session:
        account = session.get(MailAccount, int(job.payload.get("account_id") or 0))
        if account is None:
            return {"skipped": "no account"}
        folder_id = job.payload.get("folder_id")
        return sync.sync_account(session, account, folder_id=int(folder_id) if folder_id else None)


@task("mail.send", queue="notifications")
def mail_send(job: AgentTask) -> dict[str, Any]:
    outbox_id = int(job.payload.get("outbox_id") or 0)
    # First claimed and saved as "sending", so a crash after this point never sends the same mail twice.
    with transaction(write=True) as session:
        row = session.get(MailOutbox, outbox_id, with_for_update=True)
        if row is None or row.status != "queued":
            return {"skipped": "not waiting"}
        if row.send_at > datetime.now(UTC):
            q.enqueue(session, "mail.send", {"outbox_id": row.id}, queue="notifications", priority=1, run_at=row.send_at, max_attempts=1)
            return {"skipped": "not yet"}
        row.status = "sending"
    with transaction(write=True) as session:
        row = session.get(MailOutbox, outbox_id)
        if row is None:
            return {"skipped": "gone"}
        return outgoing.send(session, row)
