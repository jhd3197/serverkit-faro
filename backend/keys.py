"""Install/remove a grant's key on its servers via the agent.

Shared by the blueprint (redeem, revoke, extend, retry) and the sweeper job.
Every operation goes through the agent's ``ssh:keys:add`` / ``ssh:keys:remove``
actions (manifest permissions ``agent.command:ssh:keys:*``; agent capability
``ssh.keys``). The agent finds the user's home, writes as that user, keeps
``0600``/``0700``, serializes concurrent edits and only ever treats a missing
file as empty, so nothing here reads or rewrites an authorized_keys file
itself.

Each call updates the :class:`FaroGrantTarget` row it acted on (status, error,
attempts) and never raises; callers commit.
"""
from datetime import datetime

from app.plugins_sdk import logger
from app.plugins_sdk.agents_sdk import AgentCommandError, agents

from . import grants_lib
from .models import (FaroGrantTarget, KEY_FAILED, KEY_INSTALLED, KEY_PENDING,
                     KEY_REMOVE_FAILED, KEY_REMOVED, ROLE_JUMP, ROLE_TARGET)

log = logger(__name__)

SLUG = 'serverkit-faro'
CAPABILITY = 'ssh.keys'


def fleet():
    """The agent surface for this plugin (permission-gated by the SDK)."""
    return agents.for_plugin(SLUG)


def describe_error(err):
    """A one-line, admin-readable reason for an agent failure."""
    code = getattr(err, 'code', None)
    if code == 'PERMISSION_DENIED':
        return ("This server's agent permissions don't allow ssh:keys. Give "
                "the server Full Access, or add 'ssh:*' to its permissions.")
    if code == 'UNSUPPORTED':
        return ('The agent on this server is too old for SSH key management. '
                'Update the agent.')
    if code == 'AGENT_OFFLINE':
        return 'The agent on this server is offline.'
    message = str(err) or err.__class__.__name__
    if 'sudo required' in message:
        return ('The agent runs without root or passwordless sudo, so it '
                "can't edit other users' SSH keys.")
    return message


def targets_for(grant):
    """The grant's FaroGrantTarget rows, creating them for grants issued
    before per-server state existed (servers_json + a managed jump)."""
    rows = FaroGrantTarget.query.filter_by(grant_id=grant.id).order_by(
        FaroGrantTarget.id).all()
    if rows:
        return rows
    from app import db
    # Pre-1.1 redeemed grants may have a key out there; mark it installed so
    # revoke/expiry still tries to remove it (removing an absent key is a
    # no-op on the agent).
    status = KEY_INSTALLED if grant.public_key else KEY_PENDING
    for server in grant.server_list():
        rows.append(FaroGrantTarget(
            grant_id=grant.id, server_id=str(server.get('server_id')),
            name=server.get('name'), host=server.get('host'),
            role=ROLE_TARGET, key_status=status))
    jump = grant.jump() or {}
    if jump.get('server_id'):
        rows.append(FaroGrantTarget(
            grant_id=grant.id, server_id=str(jump['server_id']),
            name=jump.get('name'), host=jump.get('host'),
            role=ROLE_JUMP, key_status=status))
    for row in rows:
        db.session.add(row)
    db.session.flush()
    return rows


def key_options(grant, target, all_targets):
    """authorized_keys options for this grant's key on ``target``."""
    if target.role == ROLE_JUMP:
        hosts = [t.host for t in all_targets if t.role == ROLE_TARGET and t.host]
        return grants_lib.jump_key_options(hosts)
    jump = grant.jump() or {}
    return grants_lib.target_key_options(
        jump.get('host'), bool(grant.options().get('restrict_source')))


def _username_for(grant, target):
    if target.role == ROLE_JUMP:
        return (grant.jump() or {}).get('username') or grant.ssh_username
    return grant.ssh_username


def install(grant, target, all_targets, user_id=None):
    """Install (or refresh the options/expiry of) the grant key on one
    server. Returns None on success or an error string."""
    target.attempts = (target.attempts or 0) + 1
    target.updated_at = datetime.utcnow()
    try:
        params = {
            'user': _username_for(grant, target),
            'key': grants_lib.grant_key_line(grant.public_key, grant.id),
            'options': key_options(grant, target, all_targets),
            'expires_at': int((grant.expires_at - datetime(1970, 1, 1)).total_seconds()),
            'ensure_user': bool(grant.options().get('create_user')),
        }
        result = fleet().run(target.server_id, 'ssh:keys:add', params,
                             timeout=30.0, user_id=user_id, requires=CAPABILITY)
    except AgentCommandError as e:
        return _fail(target, KEY_FAILED, describe_error(e))
    except Exception as e:  # noqa: BLE001 - per-server failures are collected
        return _fail(target, KEY_FAILED, describe_error(e))
    target.key_status = KEY_INSTALLED
    target.error = None
    target.installed_at = datetime.utcnow()
    target.expiry_enforced = bool((result or {}).get('expiry_applied'))
    return None


def remove(grant, target, user_id=None):
    """Remove the grant key from one server. Returns None or an error."""
    target.attempts = (target.attempts or 0) + 1
    target.updated_at = datetime.utcnow()
    try:
        fleet().run(target.server_id, 'ssh:keys:remove', {
            'user': _username_for(grant, target),
            'key': grants_lib.grant_key_line(grant.public_key, grant.id),
        }, timeout=30.0, user_id=user_id, requires=CAPABILITY)
    except AgentCommandError as e:
        return _fail(target, KEY_REMOVE_FAILED, describe_error(e))
    except Exception as e:  # noqa: BLE001
        return _fail(target, KEY_REMOVE_FAILED, describe_error(e))
    target.key_status = KEY_REMOVED
    target.error = None
    target.removed_at = datetime.utcnow()
    return None


def remove_all(grant, targets, user_id=None):
    """Remove the key everywhere it may be (installed or a failed removal).
    Returns a list of {"name", "error"} failures."""
    if not grant.public_key:
        return []
    failed = []
    for target in targets:
        if target.key_status in (KEY_INSTALLED, KEY_REMOVE_FAILED):
            err = remove(grant, target, user_id=user_id)
            if err:
                failed.append({'name': target.name, 'error': err})
    return failed


def _fail(target, status, message):
    target.key_status = status
    target.error = message
    log.warning('faro grant key op failed on %s (%s): %s',
                target.name, target.server_id, message)
    return message
