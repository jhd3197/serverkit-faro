"""Background job handlers (serverkit-faro extension backend).

Registered via the manifest ``jobs`` block; each handler takes the ``job`` row
and must never raise (a raised handler fails the job loudly — these are
best-effort maintenance tasks, and a disabled plugin's schedules are paused by
the platform anyway).

* ``faro.expire_grants`` (:func:`expire_grants`) — sweep active/redeemed
  grants past their ``expires_at`` into 'expired'. Redeemed grants also get
  their installed public-key line removed from every granted server
  (best-effort; per-server failures are logged, not fatal). The public
  endpoints lazy-mark expiry on touch, so this job is the backstop for
  grants nobody touches again.
"""
from datetime import datetime

from app import db
from app.plugins_sdk import audit, logger

from .grants import remove_key_from_server
from .models import FaroGrant, STATUS_ACTIVE, STATUS_EXPIRED, STATUS_REDEEMED

log = logger(__name__)


def expire_grants(job):
    """Expire grants past their TTL and uninstall redeemed keys. Never raises."""
    try:
        return _run()
    except Exception as e:  # noqa: BLE001 — a maintenance job must not crash the loop
        log.warning('faro expire_grants job failed: %s', e)
        return {'error': str(e)}


def _run():
    now = datetime.utcnow()
    due = FaroGrant.query.filter(
        FaroGrant.status.in_([STATUS_ACTIVE, STATUS_REDEEMED]),
        FaroGrant.expires_at < now,
    ).all()

    expired, key_failures = 0, []
    for grant in due:
        if grant.status == STATUS_REDEEMED and grant.public_key:
            for server in grant.server_list():
                err = remove_key_from_server(server, grant.ssh_username,
                                             grant.public_key)
                if err:
                    key_failures.append({'grant_id': grant.id,
                                         'name': server.get('name'),
                                         'error': err})
        grant.status = STATUS_EXPIRED
        expired += 1
        audit('faro.grant.expired', 'faro_grant', grant.id,
              details={'name': grant.name}, user_id=grant.created_by)

    db.session.commit()
    if key_failures:
        log.warning('faro expire_grants: key removal failures: %s', key_failures)
    return {'expired': expired, 'key_failures': key_failures}
