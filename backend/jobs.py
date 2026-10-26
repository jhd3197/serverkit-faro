"""Background job handlers (serverkit-faro extension backend).

Registered via the manifest ``jobs`` block; each handler takes the ``job`` row
and must never raise (a raised handler fails the job loudly — these are
best-effort maintenance tasks, and a disabled plugin's schedules are paused by
the platform anyway).

* ``faro.expire_grants`` (:func:`expire_grants`), every 5 minutes:

  1. Sweep active/redeemed grants past their ``expires_at`` into 'expired'
     and remove their key from every server it is on. The public endpoints
     lazy-mark expiry on touch, so this is the backstop for grants nobody
     touches again.
  2. Retry key removals that failed on revoked/expired grants (agent offline,
     say), up to ``MAX_REMOVE_ATTEMPTS`` per server, so a revoke never quietly
     leaves a working key behind.
  3. Settle grants stuck in 'redeeming' (the worker died mid-install) as
     'redeemed', so whatever keys landed are tracked and revocable.
  4. Members: reconcile anyone whose grant just expired, or who has a key
     install/removal still failing (same attempt cap), and expire unused
     invite links.
"""
from datetime import datetime, timedelta

from app import db
from app.plugins_sdk import audit, logger

from . import keys, members
from .models import (FINISHED_STATUSES, FaroDeviceKey, FaroGrant, FaroGrantTarget,
                     FaroInvite, INVITE_ACTIVE, INVITE_EXPIRED, KEY_FAILED,
                     KEY_REMOVE_FAILED, STATUS_ACTIVE, STATUS_EXPIRED,
                     STATUS_REDEEMED)

log = logger(__name__)

#: ~24h of 5-minute sweeps. After that a failed removal waits for a manual
#: retry from the grant's page.
MAX_REMOVE_ATTEMPTS = 288

#: How long a redemption may sit in 'redeeming' before it's settled.
REDEEMING_TIMEOUT = timedelta(minutes=15)


def expire_grants(job):
    """Expire grants, retry failed removals, settle stuck redemptions.
    Never raises."""
    try:
        return _run()
    except Exception as e:  # noqa: BLE001 — a maintenance job must not crash the loop
        db.session.rollback()
        log.warning('faro expire_grants job failed: %s', e)
        return {'error': str(e)}


def _run():
    now = datetime.utcnow()
    expired, key_failures, retried, settled = 0, [], 0, 0
    member_ids = set()

    due = FaroGrant.query.filter(
        FaroGrant.status.in_([STATUS_ACTIVE, STATUS_REDEEMED]),
        FaroGrant.expires_at < now,
    ).all()
    for grant in due:
        grant.status = STATUS_EXPIRED
        if grant.member_id:
            member_ids.add(grant.member_id)
        else:
            targets = keys.targets_for(grant)
            for failure in keys.remove_all(grant, targets, user_id=grant.created_by):
                key_failures.append({'grant_id': grant.id, **failure})
        expired += 1
        audit('faro.grant.expired', 'faro_grant', grant.id,
              details={'name': grant.name}, user_id=grant.created_by)
        db.session.commit()

    pending = (FaroGrantTarget.query
               .join(FaroGrant, FaroGrant.id == FaroGrantTarget.grant_id)
               .filter(FaroGrant.status.in_(FINISHED_STATUSES),
                       FaroGrantTarget.key_status == KEY_REMOVE_FAILED,
                       FaroGrantTarget.attempts < MAX_REMOVE_ATTEMPTS)
               .all())
    for target in pending:
        grant = db.session.get(FaroGrant, target.grant_id)
        error = keys.remove(grant, target, user_id=grant.created_by)
        retried += 1
        if error:
            key_failures.append({'grant_id': grant.id, 'name': target.name,
                                 'error': error})
    if pending:
        db.session.commit()

    stuck = FaroGrant.query.filter(
        FaroGrant.status == 'redeeming',
        FaroGrant.redeemed_at < now - REDEEMING_TIMEOUT,
    ).all()
    for grant in stuck:
        grant.status = STATUS_REDEEMED
        settled += 1
    if stuck:
        db.session.commit()

    # Members: anyone whose grant just expired, plus anyone with a key line
    # still failing to install or remove (under the attempt cap).
    failing = (FaroDeviceKey.query
               .filter(FaroDeviceKey.key_status.in_([KEY_FAILED, KEY_REMOVE_FAILED]),
                       FaroDeviceKey.attempts < MAX_REMOVE_ATTEMPTS)
               .with_entities(FaroDeviceKey.member_id).distinct().all())
    member_ids |= {row[0] for row in failing}
    for member_id in sorted(member_ids):
        for failure in members.reconcile(member_id, max_attempts=MAX_REMOVE_ATTEMPTS):
            key_failures.append({'member_id': member_id, **failure})
        retried += 1
        db.session.commit()

    stale = FaroInvite.query.filter(FaroInvite.status == INVITE_ACTIVE,
                                    FaroInvite.expires_at < now).all()
    for invite in stale:
        invite.status = INVITE_EXPIRED
    if stale:
        db.session.commit()

    if key_failures:
        log.warning('faro expire_grants: key removal failures: %s', key_failures)
    return {'expired': expired, 'retried': retried, 'settled': settled,
            'key_failures': key_failures}
