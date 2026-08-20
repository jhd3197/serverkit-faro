"""Faro access-grant API endpoints (serverkit-faro extension backend).

Mounted under ``/api/v1/faro-grants`` via the manifest ``url_prefix``.
Implements the issuer side of **Faro Grant Protocol v1** (see Faro's
``docs/grant-links.md``):

* Admin routes (JWT / X-API-Key via ``admin_required``) mint, list and revoke
  grants. A grant is a redemption token for a bundle of managed servers; only
  the token's sha256 is stored, the raw token exists solely in the issued
  ``faro://grant`` link.
* Public routes (NO auth decorator — the token IS the auth, exactly like
  analytics' ``/collect`` pattern, plus a per-IP token bucket) serve the two
  endpoints the Faro desktop app calls: manifest fetch and public-key
  install. Unknown/redeemed/expired tokens 404 (expired is lazy-marked),
  revoked tokens 410.

Key install/removal goes through the agent SDK's ``file:read`` / ``file:write``
actions (manifest permissions ``agent.command:file:read`` /
``agent.command:file:write``) — an idempotent line edit of the grant
username's ``authorized_keys`` on every granted server.
"""
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app import db
from app.middleware.rbac import admin_required
from app.models.server import Server
from app.plugins_sdk import audit, logger
from app.plugins_sdk.agents_sdk import AgentCommandError, agents
from app.utils.client_ip import get_client_ip

from . import grants_lib
from .config import cfg_int
from .models import (FaroGrant, STATUS_ACTIVE, STATUS_EXPIRED, STATUS_REDEEMED,
                     STATUS_REVOKED)

log = logger(__name__)

bp = Blueprint('faro_grants', __name__)

SLUG = 'serverkit-faro'

#: TTL bounds for a new grant, in hours (spec: short-lived; 720h = 30 days).
DEFAULT_TTL_HOURS = 72
MAX_TTL_HOURS = 720

#: Spec: 1-64 connections per manifest.
MAX_SERVERS_PER_GRANT = 64


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fleet():
    """The agent surface for this plugin (permission-gated by the SDK)."""
    return agents.for_plugin(SLUG)


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


def authorized_keys_path(username):
    """The authorized_keys path for the grant's username."""
    if username == 'root':
        return '/root/.ssh/authorized_keys'
    return f'/home/{username}/.ssh/authorized_keys'


def install_key_on_server(server, username, key_line):
    """Append ``key_line`` to authorized_keys on one server. Returns an error
    string, or None on success. Never raises."""
    path = authorized_keys_path(username)
    try:
        try:
            payload = _fleet().run(server['server_id'], 'file:read',
                                   {'path': path}, timeout=15.0)
            content = (payload or {}).get('content') or ''
        except AgentCommandError:
            # A missing/unreadable authorized_keys is fine — start empty.
            content = ''
        new_content = grants_lib.authorized_keys_add(content, key_line)
        _fleet().run(server['server_id'], 'file:write',
                     {'path': path, 'content': new_content}, timeout=30.0)
        return None
    except AgentCommandError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - per-server failures are collected
        return str(e)


def remove_key_from_server(server, username, key_line):
    """Remove ``key_line`` from authorized_keys on one server. Returns an
    error string, or None on success. Never raises."""
    path = authorized_keys_path(username)
    try:
        payload = _fleet().run(server['server_id'], 'file:read',
                               {'path': path}, timeout=15.0)
        content = (payload or {}).get('content') or ''
        new_content = grants_lib.authorized_keys_remove(content, key_line)
        if new_content == content:
            return None  # nothing to remove (already gone)
        _fleet().run(server['server_id'], 'file:write',
                     {'path': path, 'content': new_content}, timeout=30.0)
        return None
    except AgentCommandError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return str(e)


def _grant_payload(grant):
    """The plain-dict grant shape grants_lib.build_manifest expects."""
    return {
        'name': grant.name,
        'expires_at': grant.expires_at,
        'ssh_username': grant.ssh_username,
        'path': grant.default_path,
        'jump': grant.jump(),
    }


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
@bp.route('/grants', methods=['POST'])
@admin_required
def create_grant():
    """Mint a grant over a bundle of managed servers. Every server must have
    a live agent — redemption installs keys through it, so issuing against an
    offline agent would hand out a link that can only half-work."""
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    server_ids = data.get('server_ids')
    if not isinstance(server_ids, list) or not server_ids:
        return jsonify({'error': 'server_ids must be a non-empty list'}), 400
    if len(server_ids) > MAX_SERVERS_PER_GRANT:
        return jsonify(
            {'error': f'a grant covers at most {MAX_SERVERS_PER_GRANT} servers'}
        ), 400

    try:
        ttl_hours = int(data.get('ttl_hours') or DEFAULT_TTL_HOURS)
    except (TypeError, ValueError):
        ttl_hours = DEFAULT_TTL_HOURS
    ttl_hours = max(1, min(MAX_TTL_HOURS, ttl_hours))

    ssh_username = (data.get('ssh_username') or 'root').strip() or 'root'
    path = (data.get('path') or '').strip() or None

    jump = data.get('jump') or None
    if jump is not None:
        if not isinstance(jump, dict) or not (jump.get('host') or '').strip():
            return jsonify({'error': 'jump must be an object with a host'}), 400
        jump = {
            'host': jump['host'].strip(),
            'port': int(jump.get('port') or 22),
            'username': (jump.get('username') or '').strip() or None,
        }

    servers = Server.query.filter(Server.id.in_(server_ids)).all()
    found_ids = {s.id for s in servers}
    missing = [sid for sid in server_ids if sid not in found_ids]
    if missing:
        return jsonify({'error': 'unknown server ids', 'missing': missing}), 400

    fleet = _fleet()
    offline = [s.name for s in servers if not fleet.is_online(s.id)]
    if offline:
        return jsonify({
            'error': 'every server must have an online agent to issue a grant',
            'offline': offline,
        }), 400

    servers_snapshot = [{
        'server_id': s.id,
        'name': s.name,
        'host': s.ip_address or s.hostname,
    } for s in servers]

    token = grants_lib.new_token()
    grant = FaroGrant(
        name=name,
        token_hash=grants_lib.hash_token(token),
        ssh_username=ssh_username,
        default_path=path,
        status=STATUS_ACTIVE,
        created_by=_current_user_id(),
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
    )
    grant.set_servers(servers_snapshot)
    grant.set_jump(jump)
    db.session.add(grant)
    db.session.commit()

    audit('faro.grant.issued', 'faro_grant', grant.id, details={
        'name': name,
        'servers': [s['name'] for s in servers_snapshot],
        'ssh_username': ssh_username,
        'ttl_hours': ttl_hours,
    })

    link = grants_lib.build_faro_link(_panel_base_url(), token, name)
    return jsonify({'grant': grant.to_dict(), 'link': link}), 201


@bp.route('/grants', methods=['GET'])
@admin_required
def list_grants():
    """All grants, newest first."""
    grants = FaroGrant.query.order_by(FaroGrant.created_at.desc()).all()
    return jsonify({'grants': [g.to_dict() for g in grants]}), 200


@bp.route('/grants/<int:grant_id>/revoke', methods=['POST'])
@admin_required
def revoke_grant(grant_id):
    """Revoke a grant: remove the installed key (best-effort, per-server
    failures collected, not fatal) and mark it revoked. Idempotent."""
    grant = FaroGrant.query.get(grant_id)
    if not grant:
        return jsonify({'error': 'grant not found'}), 404
    if grant.status == STATUS_REVOKED:
        return jsonify({'grant': grant.to_dict(), 'failed': []}), 200

    failed = []
    if grant.public_key:
        for server in grant.server_list():
            err = remove_key_from_server(server, grant.ssh_username,
                                         grant.public_key)
            if err:
                failed.append({'name': server.get('name'), 'error': err})

    grant.status = STATUS_REVOKED
    db.session.commit()

    audit('faro.grant.revoked', 'faro_grant', grant.id, details={
        'name': grant.name,
        'key_removed': bool(grant.public_key) and not failed,
        'failures': failed,
    })
    return jsonify({'grant': grant.to_dict(), 'failed': failed}), 200


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
    if grant.status == STATUS_REDEEMED:
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
        panel_url=request.host,
    )
    return jsonify(manifest), 200


@bp.route('/.well-known/faro-grant/<token>/key', methods=['POST'])
def grant_install_key(token):
    """Faro Grant Protocol step 2: public-key upload (post-consent).

    Installs the key on every granted server, then burns the token. Partial
    success is reported honestly (``failed`` entries); total failure is a
    502 and stores nothing, so the token stays redeemable.
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

    installed, failed = [], []
    for server in grant.server_list():
        error = install_key_on_server(server, grant.ssh_username, key_line)
        if error:
            failed.append({'name': server.get('name'), 'error': error})
        else:
            installed.append(server.get('name'))

    if not installed:
        return jsonify({
            'error': 'key install failed on every server in the grant',
            'failed': failed,
        }), 502

    grant.public_key = key_line
    grant.status = STATUS_REDEEMED
    grant.redeemed_at = datetime.utcnow()
    db.session.commit()

    audit('faro.grant.redeemed', 'faro_grant', grant.id, details={
        'name': grant.name,
        'installed': installed,
        'failed': failed,
    })
    return jsonify({'installed': installed, 'failed': failed}), 200
