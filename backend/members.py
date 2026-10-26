"""Member access: who gets which key where, and making the servers match.

A member's access is the cross product of their **active devices** and their
**live grants** (member grants with status 'redeemed' that haven't expired).
Each (device, server, login) is one authorized_keys line — one
:class:`FaroDeviceKey` row. Two grants can want the same line (the same server
and login, or a shared bridge); :func:`desired_keys` merges them:

* a server that is a granted server in any grant gets granted-server options
  (a bridge-only entry is stricter, so the looser target options win);
* ``from=`` is kept only if every grant wanting the line restricts it, and
  then lists every bridge address;
* a bridge's ``permitopen`` is the union of the servers behind it;
* the expiry is the latest of the grants'.

:func:`reconcile` then makes the servers match: install what's missing or
changed, remove what nothing wants anymore. It is the only thing that touches a
member's keys, so revoking a grant or a device, approving a device, extending
or expiring a grant are all "change the rows, then reconcile".
"""
import json
from datetime import datetime

from app import db

from . import grants_lib, keys
from .models import (DEVICE_ACTIVE, FINISHED_STATUSES, FaroDevice, FaroDeviceKey,
                     FaroGrant, KEY_FAILED, KEY_INSTALLED, KEY_PENDING,
                     KEY_REMOVE_FAILED, KEY_REMOVED, ROLE_JUMP, ROLE_TARGET,
                     STATUS_REDEEMED)


def live_grants(member_id, now=None):
    now = now or datetime.utcnow()
    return (FaroGrant.query
            .filter(FaroGrant.member_id == member_id,
                    FaroGrant.status == STATUS_REDEEMED,
                    FaroGrant.expires_at > now)
            .order_by(FaroGrant.id).all())


def _merge(want, key, spec):
    have = want.get(key)
    if have is None:
        want[key] = spec
        return
    if spec['role'] == ROLE_TARGET or have['role'] == ROLE_TARGET:
        target_from = [s['from'] for s in (have, spec) if s['role'] == ROLE_TARGET]
        have['from'] = (None if any(f is None for f in target_from)
                        else set().union(*target_from))
        have['role'] = ROLE_TARGET
    have['permit'] |= spec['permit']
    have['expires_at'] = max(have['expires_at'], spec['expires_at'])
    have['create_user'] = have['create_user'] or spec['create_user']
    have['grant_ids'] |= spec['grant_ids']


def desired_keys(member_id, now=None):
    """{(device_id, server_id, username): spec} for everything the member's
    active devices should have right now."""
    grants = live_grants(member_id, now)
    devices = FaroDevice.query.filter_by(member_id=member_id, status=DEVICE_ACTIVE).all()
    want = {}
    for grant in grants:
        options = grant.options()
        jump = grant.jump() or {}
        servers = grant.server_list()
        restrict = ({jump['host']} if options.get('restrict_source') and jump.get('host')
                    else None)
        create_user = bool(options.get('create_user'))
        for device in devices:
            for server in servers:
                _merge(want, (device.id, str(server['server_id']), grant.ssh_username), {
                    'role': ROLE_TARGET, 'name': server.get('name'),
                    'host': server.get('host'), 'from': restrict and set(restrict),
                    'permit': set(), 'expires_at': grant.expires_at,
                    'create_user': create_user, 'grant_ids': {grant.id},
                })
            if jump.get('server_id'):
                _merge(want, (device.id, str(jump['server_id']),
                              jump.get('username') or grant.ssh_username), {
                    'role': ROLE_JUMP, 'name': jump.get('name'), 'host': jump.get('host'),
                    'from': None,
                    'permit': {s['host'] for s in servers if s.get('host')},
                    'expires_at': grant.expires_at,
                    'create_user': create_user, 'grant_ids': {grant.id},
                })
    return want


def options_for(spec):
    if spec['role'] == ROLE_JUMP:
        return grants_lib.jump_key_options(sorted(spec['permit']))
    options = ['no-agent-forwarding', 'no-X11-forwarding']
    if spec['from']:
        options.append(grants_lib.from_option(spec['from']))
    return options


def reconcile(member_id, user_id=None, max_attempts=None):
    """Make the servers match :func:`desired_keys` for one member.

    Returns a list of {"name", "device_id", "error"} failures. Rows whose
    consecutive failures reached ``max_attempts`` are skipped (the sweeper
    passes a cap; a manual retry passes none). Callers commit.
    """
    want = desired_keys(member_id)
    rows = {(r.device_id, r.server_id, r.username): r
            for r in FaroDeviceKey.query.filter_by(member_id=member_id).all()}
    devices = {d.id: d for d in FaroDevice.query.filter_by(member_id=member_id).all()}
    failures = []

    def capped(row):
        return max_attempts is not None and (row.attempts or 0) >= max_attempts

    for key, spec in want.items():
        device_id, server_id, username = key
        row = rows.get(key)
        if row is None:
            row = FaroDeviceKey(member_id=member_id, device_id=device_id,
                                server_id=server_id, username=username,
                                key_status=KEY_PENDING, attempts=0)
            db.session.add(row)
        row.name, row.host, row.role = spec['name'], spec['host'], spec['role']
        options = options_for(spec)
        options_json = json.dumps(options)
        up_to_date = (row.key_status == KEY_INSTALLED
                      and row.options_json == options_json
                      and row.expires_at == spec['expires_at'])
        if up_to_date or (row.key_status == KEY_FAILED and capped(row)):
            continue
        error = keys.add_key(
            row, username,
            grants_lib.device_key_line(devices[device_id].public_key, device_id),
            options, spec['expires_at'], ensure_user=spec['create_user'],
            user_id=user_id)
        if error:
            failures.append({'name': row.name, 'device_id': device_id, 'error': error})
        else:
            row.options_json = options_json
            row.expires_at = spec['expires_at']

    for key, row in rows.items():
        if key in want:
            continue
        if row.key_status in (KEY_PENDING, KEY_FAILED):
            row.key_status = KEY_REMOVED  # never made it onto the server
            row.error = None
            continue
        if row.key_status not in (KEY_INSTALLED, KEY_REMOVE_FAILED):
            continue
        if row.key_status == KEY_REMOVE_FAILED and capped(row):
            continue
        device = devices.get(row.device_id)
        error = keys.remove_key(
            row, row.username,
            grants_lib.device_key_line(device.public_key, device.id),
            user_id=user_id)
        if error:
            failures.append({'name': row.name, 'device_id': row.device_id, 'error': error})
    db.session.flush()
    return failures


def grant_rows(grant):
    """The device-key rows that carry a member grant's access, each tagged
    with its device label for display."""
    servers = {str(s['server_id']): grant.ssh_username for s in grant.server_list()}
    jump = grant.jump() or {}
    if jump.get('server_id'):
        servers.setdefault(str(jump['server_id']),
                           jump.get('username') or grant.ssh_username)
    rows = (FaroDeviceKey.query
            .filter(FaroDeviceKey.member_id == grant.member_id,
                    FaroDeviceKey.server_id.in_(list(servers)))
            .order_by(FaroDeviceKey.id).all())
    labels = {d.id: d.label for d in
              FaroDevice.query.filter_by(member_id=grant.member_id).all()}
    out = []
    for row in rows:
        if servers.get(row.server_id) != row.username:
            continue
        row.device_label = labels.get(row.device_id)
        out.append(row)
    return out


def grant_health(grant, rows):
    """Like models.grant_health, for a member grant's device-key rows.

    A live grant is ``partial`` when one of its keys failed to install. An
    ended grant needs ``cleanup`` while a removal failed. A key that stays
    installed because another live grant still wants it is fine either way.
    """
    statuses = {r.key_status for r in rows}
    if grant.status in FINISHED_STATUSES:
        return 'cleanup' if KEY_REMOVE_FAILED in statuses else 'ok'
    if grant.status == STATUS_REDEEMED and statuses & {KEY_FAILED, KEY_PENDING}:
        return 'partial'
    return 'ok'


def invite_connections(member, grants):
    """Manifest connections for a member's invite (one per grant x server),
    plus how each one maps to device-key rows so the key upload can report
    which connections actually work.

    Returns ``(connections, needs)`` where ``needs[label]`` is the list of
    (server_id, username) lines that connection requires (the server, plus
    the bridge when there is one).
    """
    connections, needs, seen = [], {}, set()
    for grant in grants:
        jump = grant.jump() or {}
        for server in grant.server_list():
            label = server.get('name') or server['host']
            if label in seen:
                label = f'{label} ({grant.ssh_username})'
            if label in seen:
                label = f'{label} · {grant.name}'
            seen.add(label)
            conn = {
                'name': label,
                'protocol': 'sftp',
                'host': server['host'],
                'port': 22,
                'username': grant.ssh_username,
            }
            if grant.default_path:
                conn['path'] = grant.default_path
            lines = [(str(server['server_id']), grant.ssh_username)]
            if jump.get('host'):
                jump_user = jump.get('username') or grant.ssh_username
                conn['jump'] = {'host': jump['host'], 'port': int(jump.get('port') or 22),
                                'username': jump_user}
                if jump.get('server_id'):
                    lines.append((str(jump['server_id']), jump_user))
            connections.append(conn)
            needs[label] = lines
    return connections, needs


def invite_manifest(member, grants, panel_url):
    connections, _ = invite_connections(member, grants)
    manifest = {
        'version': 1,
        'issuer': f'ServerKit · {grants_lib._host_of(panel_url)}',
        'name': member.name,
        'group': member.name,
    }
    latest = max((g.expires_at for g in grants), default=None)
    if latest is not None:
        manifest['expires_at'] = grants_lib._rfc3339(latest)
    manifest['auth'] = {'type': 'key-install'}
    manifest['connections'] = connections[:64]
    return manifest
