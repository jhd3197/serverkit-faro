"""Faro access-grant API endpoints (serverkit-faro extension backend).

Mounted under ``/api/v1/faro-grants`` via the manifest ``url_prefix``.
Implements the issuer side of **Faro Grant Protocol v1** (see Faro's
``docs/grant-links.md``):

* Admin routes (JWT / X-API-Key via ``admin_required``) mint, list, inspect,
  extend, retry, revoke and delete grants. A grant is a redemption token for a
  bundle of managed servers; only the token's sha256 is stored, the raw token
  exists solely in the issued ``faro://grant`` link.
* Public routes (NO auth decorator — the token IS the auth, exactly like
  analytics' ``/collect`` pattern, plus a per-IP token bucket) serve the two
  endpoints the Faro desktop app calls: manifest fetch and public-key
  install. Unknown/redeemed/expired tokens 404 (expired is lazy-marked),
  revoked tokens 410.

Key install/removal goes through :mod:`keys` (the agent's ``ssh:keys:*``
actions), with one :class:`FaroGrantTarget` row per server tracking whether
the key is actually there.
"""
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app import db
from app.middleware.rbac import admin_required
from app.models.server import Server
from app.plugins_sdk import audit, logger
from app.utils.client_ip import get_client_ip

from . import grants_lib, keys
from .config import cfg_int
from .models import (FINISHED_STATUSES, FaroGrant, FaroGrantTarget,
                     KEY_FAILED, KEY_INSTALLED, KEY_PENDING, KEY_REMOVE_FAILED,
                     ROLE_JUMP, ROLE_TARGET, STATUS_ACTIVE, STATUS_EXPIRED,
                     STATUS_REDEEMED, STATUS_REVOKED, grant_health)

log = logger(__name__)

bp = Blueprint('faro_grants', __name__)

#: TTL bounds for a new grant, in hours (spec: short-lived; 720h = 30 days).
DEFAULT_TTL_HOURS = 72
MAX_TTL_HOURS = 720

#: Spec: 1-64 connections per manifest.
MAX_SERVERS_PER_GRANT = 64

#: Transient state while one request installs keys for a token, so a
#: double-clicked Accept can't redeem the same link twice.
STATUS_REDEEMING = 'redeeming'


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _current_user_id():
    """Best-effort id of the calling admin (None outside a JWT context)."""
    try:
        from app.plugins_sdk import current_user
        user = current_user()
        return getattr(user, 'id', None)
    except Exception:  # noqa: BLE001 - no request context / anonymous
        return None


def _panel_base_url():
    """Best public base URL for issued links, preferring a configured public
    URL over the request host so a copied link works off-panel."""
    from flask import current_app as _app
    configured = (_app.config.get('SERVERKIT_PUBLIC_URL')
                  or _app.config.get('PUBLIC_URL') or '').strip()
    if configured:
        return configured.rstrip('/')
    return (request.host_url or '').rstrip('/')


def _server_host(server):
    return server.ip_address or server.hostname


def _grant_payload(grant):
    """The plain-dict grant shape grants_lib.build_manifest expects."""
    return {
        'name': grant.name,
        'expires_at': grant.expires_at,
        'ssh_username': grant.ssh_username,
        'path': grant.default_path,
        'jump': grant.jump(),
    }


def _ttl_hours(value):
    try:
        hours = int(value or DEFAULT_TTL_HOURS)
    except (TypeError, ValueError):
        raise ValueError('ttl_hours must be a whole number of hours') from None
    return max(1, min(MAX_TTL_HOURS, hours))


def _grant_json(grant, targets=None):
    if targets is None:
        targets = keys.targets_for(grant)
    return grant.to_dict(targets=targets)


def _get_grant_or_404(grant_id):
    grant = db.session.get(FaroGrant, grant_id)
    if not grant:
        return None, (jsonify({'error': 'grant not found'}), 404)
    return grant, None


# --------------------------------------------------------------------------- #
# In-process token-bucket rate limiter for the public endpoints (per IP).
# Mirrors serverkit-analytics' /collect limiter: single-worker safe, swept so
# the map can't grow unbounded.
# --------------------------------------------------------------------------- #
_rl_lock = threading.Lock()
_rl_buckets = {}          # ip -> [tokens, last_refill_epoch]
_rl_last_sweep = 0.0


def _rate_ok(ip):
    """Return True if this hit is under the per-IP per-minute limit."""
    rate = cfg_int('public_rate_per_min', minimum=1)
    capacity = float(rate)
    refill = capacity / 60.0
    now = time.monotonic()
    global _rl_last_sweep
    with _rl_lock:
        if now - _rl_last_sweep >= 300:
            _rl_last_sweep = now
            stale = [k for k, (_, last) in _rl_buckets.items()
                     if now - last > 600]
            for k in stale:
                _rl_buckets.pop(k, None)
        tokens, last = _rl_buckets.get(ip or '?', (capacity, now))
        tokens = min(capacity, tokens + (now - last) * refill)
        if tokens < 1.0:
            _rl_buckets[ip or '?'] = (tokens, now)
            return False
        _rl_buckets[ip or '?'] = (tokens - 1.0, now)
        return True


def reset_rate_limits():
    """Test/shutdown helper."""
    with _rl_lock:
        _rl_buckets.clear()


# --------------------------------------------------------------------------- #
# admin routes (JWT / X-API-Key)
# --------------------------------------------------------------------------- #
@bp.route('/servers', methods=['GET'])
@admin_required
def list_grantable_servers():
    """Managed servers with what the create form needs to know: reachable
    host, agent online, and whether the agent can manage SSH keys."""
    fleet = keys.fleet()
    out = []
    for server in Server.query.order_by(Server.name).all():
        online = fleet.is_online(server.id)
        out.append({
            'id': server.id,
            'name': server.name,
            'host': _server_host(server),
            'online': online,
            'ssh_keys': online and fleet.has_capability(server.id, keys.CAPABILITY),
        })
    return jsonify({'servers': out}), 200


@bp.route('/grants', methods=['POST'])
@admin_required
def create_grant():
    """Mint a grant over a bundle of managed servers (plus an optional managed
    bridge server for the jump hop). Every server must have an online agent
    that can manage SSH keys — redemption installs keys through it, so issuing
    against one that can't would hand out a link that can only half-work."""
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if len(name) > 255:
        return jsonify({'error': 'name is too long'}), 400

    server_ids = data.get('server_ids')
    if not isinstance(server_ids, list) or not server_ids:
        return jsonify({'error': 'server_ids must be a non-empty list'}), 400
    server_ids = list(dict.fromkeys(str(sid) for sid in server_ids))
    if len(server_ids) > MAX_SERVERS_PER_GRANT:
        return jsonify(
            {'error': f'a grant covers at most {MAX_SERVERS_PER_GRANT} servers'}
        ), 400

    try:
        ttl_hours = _ttl_hours(data.get('ttl_hours'))
        ssh_username = grants_lib.validate_username(
            (data.get('ssh_username') or 'root').strip() or 'root')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    path = (data.get('path') or '').strip() or None
    if path and (not path.startswith('/') or len(path) > 512):
        return jsonify({'error': 'path must be an absolute path'}), 400

    jump_in = data.get('jump') or None
    jump_server_id = None
    jump_username = None
    if jump_in is not None:
        if not isinstance(jump_in, dict) or not jump_in.get('server_id'):
            return jsonify({
                'error': 'jump must name a managed bridge server ({"server_id": ...}) '
                         'so its key can be installed and removed with the grant',
            }), 400
        jump_server_id = str(jump_in['server_id'])
        if jump_server_id in server_ids:
            return jsonify({'error': 'the bridge server cannot also be a granted server'}), 400
        try:
            jump_username = grants_lib.validate_username(
                (jump_in.get('username') or '').strip() or ssh_username)
        except ValueError as e:
            return jsonify({'error': f'jump: {e}'}), 400

    wanted = server_ids + ([jump_server_id] if jump_server_id else [])
    servers = {s.id: s for s in Server.query.filter(Server.id.in_(wanted)).all()}
    missing = [sid for sid in wanted if sid not in servers]
    if missing:
        return jsonify({'error': 'unknown server ids', 'missing': missing}), 400

    no_host = [servers[sid].name for sid in wanted if not _server_host(servers[sid])]
    if no_host:
        return jsonify({'error': 'these servers have no reachable address yet',
                        'servers': no_host}), 400
    try:
        for sid in wanted:
            grants_lib.validate_host(_server_host(servers[sid]))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    fleet = keys.fleet()
    offline = [servers[sid].name for sid in wanted if not fleet.is_online(sid)]
    if offline:
        return jsonify({
            'error': 'every server must have an online agent to issue a grant',
            'offline': offline,
        }), 400
    unsupported = [servers[sid].name for sid in wanted
                   if not fleet.has_capability(sid, keys.CAPABILITY)]
    if unsupported:
        return jsonify({
            'error': "these servers' agents can't manage SSH keys (update the "
                     'agent, and make sure it runs as root or with passwordless sudo)',
            'unsupported': unsupported,
        }), 400

    servers_snapshot = [{
        'server_id': sid,
        'name': servers[sid].name,
        'host': _server_host(servers[sid]),
    } for sid in server_ids]
    jump = None
    if jump_server_id:
        bridge = servers[jump_server_id]
        jump = {
            'server_id': jump_server_id,
            'name': bridge.name,
            'host': _server_host(bridge),
            'port': 22,
            'username': jump_username,
        }

    token = grants_lib.new_token()
    now = datetime.utcnow()
    grant = FaroGrant(
        name=name,
        token_hash=grants_lib.hash_token(token),
        ssh_username=ssh_username,
        default_path=path,
        status=STATUS_ACTIVE,
        created_by=_current_user_id(),
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    grant.set_servers(servers_snapshot)
    grant.set_jump(jump)
    grant.set_options({
        'restrict_source': bool(data.get('restrict_source')) and bool(jump),
        'create_user': bool(data.get('create_user')) and ssh_username != 'root',
    })
    db.session.add(grant)
    db.session.flush()

    targets = [FaroGrantTarget(grant_id=grant.id, server_id=s['server_id'],
                               name=s['name'], host=s['host'], role=ROLE_TARGET)
               for s in servers_snapshot]
    if jump:
        targets.append(FaroGrantTarget(grant_id=grant.id, server_id=jump['server_id'],
                                       name=jump['name'], host=jump['host'],
                                       role=ROLE_JUMP))
    db.session.add_all(targets)
    db.session.commit()

    audit('faro.grant.issued', 'faro_grant', grant.id, details={
        'name': name,
        'servers': [s['name'] for s in servers_snapshot],
        'bridge': jump['name'] if jump else None,
        'ssh_username': ssh_username,
        'ttl_hours': ttl_hours,
    })

    link = grants_lib.build_faro_link(_panel_base_url(), token, name)
    return jsonify({'grant': _grant_json(grant, targets), 'link': link}), 201


@bp.route('/grants', methods=['GET'])
@admin_required
def list_grants():
    """All grants, newest first, each with its per-server key state."""
    grants = FaroGrant.query.order_by(FaroGrant.created_at.desc()).all()
    by_grant = {}
    if grants:
        rows = FaroGrantTarget.query.filter(
            FaroGrantTarget.grant_id.in_([g.id for g in grants])
        ).order_by(FaroGrantTarget.id).all()
        for row in rows:
            by_grant.setdefault(row.grant_id, []).append(row)
    out = []
    backfilled = False
    for grant in grants:
        targets = by_grant.get(grant.id)
        if targets is None:
            targets = keys.targets_for(grant)
            backfilled = True
        out.append(grant.to_dict(targets=targets))
    if backfilled:
        db.session.commit()
    return jsonify({'grants': out}), 200


@bp.route('/grants/<int:grant_id>', methods=['GET'])
@admin_required
def get_grant(grant_id):
    grant, err = _get_grant_or_404(grant_id)
    if err:
        return err
    data = _grant_json(grant)
    db.session.commit()  # persist a backfill, if any
    return jsonify({'grant': data}), 200


@bp.route('/grants/<int:grant_id>/revoke', methods=['POST'])
@admin_required
def revoke_grant(grant_id):
    """Revoke a grant and remove its key everywhere it is installed.

    Per-server failures are kept on the target rows (``remove_failed``) and
    retried by the sweeper; calling revoke again on a revoked grant retries
    the removal now.
    """
    grant, err = _get_grant_or_404(grant_id)
    if err:
        return err
    targets = keys.targets_for(grant)
    was = grant.status
    if grant.status != STATUS_REVOKED:
        grant.status = STATUS_REVOKED
        grant.revoked_at = datetime.utcnow()
    failed = keys.remove_all(grant, targets, user_id=_current_user_id())
    db.session.commit()

    if was != STATUS_REVOKED:
        audit('faro.grant.revoked', 'faro_grant', grant.id, details={
            'name': grant.name,
            'key_removed': bool(grant.public_key) and not failed,
            'failures': failed,
        })
    return jsonify({'grant': grant.to_dict(targets=targets), 'failed': failed}), 200


@bp.route('/grants/<int:grant_id>/extend', methods=['POST'])
@admin_required
def extend_grant(grant_id):
    """Set a new expiry, ``ttl_hours`` from now. For a redeemed grant the key
    is re-installed everywhere so sshd's own expiry-time moves with it."""
    grant, err = _get_grant_or_404(grant_id)
    if err:
        return err
    if grant.status not in (STATUS_ACTIVE, STATUS_REDEEMED):
        return jsonify({'error': f'a {grant.status} grant cannot be extended'}), 409
    data = request.get_json(silent=True) or {}
    try:
        ttl_hours = _ttl_hours(data.get('ttl_hours'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    old_expiry = grant.expires_at
    grant.expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    targets = keys.targets_for(grant)
    failed = []
    if grant.status == STATUS_REDEEMED and grant.public_key:
        for target in targets:
            if target.key_status == KEY_INSTALLED:
                error = keys.install(grant, target, targets, user_id=_current_user_id())
                if error:
                    failed.append({'name': target.name, 'error': error})
    db.session.commit()

    audit('faro.grant.extended', 'faro_grant', grant.id, details={
        'name': grant.name,
        'from': old_expiry.isoformat() if old_expiry else None,
        'to': grant.expires_at.isoformat(),
        'failures': failed,
    })
    return jsonify({'grant': grant.to_dict(targets=targets), 'failed': failed}), 200


@bp.route('/grants/<int:grant_id>/retry', methods=['POST'])
@admin_required
def retry_grant(grant_id):
    """Retry whatever is outstanding: installs that failed on a redeemed
    grant, or removals that failed on a revoked/expired one."""
    grant, err = _get_grant_or_404(grant_id)
    if err:
        return err
    targets = keys.targets_for(grant)
    user_id = _current_user_id()
    failed = []
    if grant.status in FINISHED_STATUSES:
        failed = keys.remove_all(grant, targets, user_id=user_id)
    elif grant.status == STATUS_REDEEMED and grant.public_key:
        for target in targets:
            if target.key_status in (KEY_PENDING, KEY_FAILED):
                error = keys.install(grant, target, targets, user_id=user_id)
                if error:
                    failed.append({'name': target.name, 'error': error})
    else:
        return jsonify({'error': 'nothing to retry until the link is redeemed'}), 409
    db.session.commit()
    return jsonify({'grant': grant.to_dict(targets=targets), 'failed': failed}), 200


@bp.route('/grants/<int:grant_id>', methods=['DELETE'])
@admin_required
def delete_grant(grant_id):
    """Delete a finished grant's record. Refused while a key may still be on
    a server — clean up (retry) first, so a key never outlives its record."""
    grant, err = _get_grant_or_404(grant_id)
    if err:
        return err
    if grant.status not in FINISHED_STATUSES:
        return jsonify({'error': 'revoke the grant before deleting it'}), 409
    targets = keys.targets_for(grant)
    if grant_health(grant, targets) == 'cleanup':
        return jsonify({'error': 'the key is still installed on some servers; '
                                 'retry the cleanup first'}), 409
    for target in targets:
        db.session.delete(target)
    db.session.delete(grant)
    db.session.commit()
    audit('faro.grant.deleted', 'faro_grant', grant_id, details={'name': grant.name})
    return jsonify({'deleted': grant_id}), 200


# --------------------------------------------------------------------------- #
# public protocol routes (NO auth — the token IS the auth; per-IP rate limit)
# --------------------------------------------------------------------------- #
def _public_grant_or_error(token):
    """Resolve a redemption token to its grant, enforcing lifecycle state.

    Returns ``(grant, None)`` or ``(None, response)``. Unknown, redeemed and
    expired tokens are indistinguishable 404s (a redeemed/expired token must
    not confirm it ever existed); revoked gets a distinct 410 per spec.
    Expiry is lazy-marked so the sweeper job stays a backstop, not a gate.
    """
    grant = FaroGrant.query.filter_by(
        token_hash=grants_lib.hash_token(token)).first()
    if not grant:
        return None, (jsonify({'error': 'unknown grant'}), 404)
    if grant.status == STATUS_REVOKED:
        return None, (jsonify({'error': 'grant revoked'}), 410)
    if grant.status != STATUS_ACTIVE:
        return None, (jsonify({'error': 'unknown grant'}), 404)
    if grant.is_past_expiry():
        grant.status = STATUS_EXPIRED
        db.session.commit()
        return None, (jsonify({'error': 'unknown grant'}), 404)
    return grant, None


@bp.route('/.well-known/faro-grant/<token>', methods=['GET'])
def grant_manifest(token):
    """Faro Grant Protocol step 1: manifest fetch (pre-consent)."""
    if not _rate_ok(get_client_ip()):
        return jsonify({'error': 'rate limited'}), 429
    grant, err = _public_grant_or_error(token)
    if err:
        return err
    manifest = grants_lib.build_manifest(
        issuer_name='ServerKit',
        grant=_grant_payload(grant),
        servers=grant.server_list(),
        panel_url=_panel_base_url(),
    )
    return jsonify(manifest), 200


@bp.route('/.well-known/faro-grant/<token>/key', methods=['POST'])
def grant_install_key(token):
    """Faro Grant Protocol step 2: public-key upload (post-consent).

    Claims the token first (active -> redeeming, atomically) so a second
    concurrent upload gets a 404 instead of installing a second key. Then
    installs the key on the bridge (if any) and every granted server.

    Success needs the bridge (without it nothing connects) and at least one
    granted server; the rest is reported in ``failed`` and stays retryable
    from the panel. Anything less is rolled back — keys that did land are
    removed, the token goes back to active — and answered 502.
    """
    if not _rate_ok(get_client_ip()):
        return jsonify({'error': 'rate limited'}), 429
    grant, err = _public_grant_or_error(token)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    try:
        key_line = grants_lib.validate_public_key(data.get('public_key'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    now = datetime.utcnow()
    claimed = FaroGrant.query.filter_by(id=grant.id, status=STATUS_ACTIVE).update(
        {'status': STATUS_REDEEMING, 'public_key': key_line, 'redeemed_at': now},
        synchronize_session=False)
    db.session.commit()
    if claimed != 1:
        return jsonify({'error': 'unknown grant'}), 404
    db.session.refresh(grant)

    targets = keys.targets_for(grant)
    by_role = {ROLE_JUMP: [], ROLE_TARGET: []}
    for target in targets:
        error = keys.install(grant, target, targets, user_id=grant.created_by)
        by_role[target.role].append((target, error))

    installed = [t.name for t, e in by_role[ROLE_TARGET] if not e]
    failed = [{'name': t.name, 'error': e}
              for role in (ROLE_JUMP, ROLE_TARGET) for t, e in by_role[role] if e]
    bridge_ok = all(not e for _, e in by_role[ROLE_JUMP])

    if not installed or not bridge_ok:
        for target in targets:
            if target.key_status == KEY_INSTALLED:
                keys.remove(grant, target, user_id=grant.created_by)
        for target in targets:
            if target.key_status != KEY_REMOVE_FAILED:
                target.key_status = KEY_PENDING  # error text is kept for the UI
        stuck = [t for t in targets if t.key_status == KEY_REMOVE_FAILED]
        if stuck:
            # Couldn't roll back cleanly: keep the grant redeemed so the
            # leftover key stays tracked (and revocable) instead of
            # orphaning it behind a reusable token.
            grant.status = STATUS_REDEEMED
            grant.redeemed_ip = get_client_ip()
        else:
            grant.status = STATUS_ACTIVE
            grant.public_key = None
            grant.redeemed_at = None
        db.session.commit()
        reason = ('key install failed on the bridge server' if not bridge_ok
                  else 'key install failed on every server in the grant')
        audit('faro.grant.redeem_failed', 'faro_grant', grant.id, details={
            'name': grant.name, 'failed': failed,
        }, user_id=grant.created_by)
        return jsonify({'error': reason, 'failed': failed}), 502

    grant.status = STATUS_REDEEMED
    grant.redeemed_ip = get_client_ip()
    db.session.commit()

    audit('faro.grant.redeemed', 'faro_grant', grant.id, details={
        'name': grant.name,
        'installed': installed,
        'failed': failed,
        'fingerprint': grants_lib.key_fingerprint(key_line),
        'ip': grant.redeemed_ip,
    }, user_id=grant.created_by)
    return jsonify({'installed': installed, 'failed': failed}), 200
