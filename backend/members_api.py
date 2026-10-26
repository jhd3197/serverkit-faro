"""Member / device / invite routes (serverkit-faro extension backend).

Registered on the same blueprint as :mod:`grants` (``/api/v1/faro-grants``).

* A **member** is a person. Grants issued *to a member* carry no link of their
  own; every active device of the member gets the access.
* A **device** is one key the member uploaded from Faro by opening an
  **invite** link ("add a device"). With ``require_approval`` (the default)
  the device waits as ``pending`` until an admin approves it, so a leaked
  invite link only produces a request, not access.
* Every change here ends in :func:`members.reconcile`, which makes the
  servers' authorized_keys match.

The two public protocol endpoints in :mod:`grants` hand invite tokens to
:func:`invite_manifest_response` / :func:`invite_key_response`, so one
``faro://grant`` link format serves both one-time grants and invites.
"""
from datetime import datetime, timedelta

from flask import jsonify, request

from app import db
from app.middleware.rbac import admin_required
from app.plugins_sdk import audit
from app.utils.client_ip import get_client_ip

from . import grants_lib, members
from .grants import (MAX_TTL_HOURS, _current_user_id, _grant_json, _member_failures,
                     _panel_base_url, bp)
from .models import (DEVICE_ACTIVE, DEVICE_PENDING, DEVICE_REJECTED, DEVICE_REVOKED,
                     FaroDevice, FaroDeviceKey, FaroGrant, FaroInvite, FaroMember,
                     INVITE_ACTIVE, INVITE_EXPIRED, INVITE_REVOKED, INVITE_USED,
                     KEY_INSTALLED, KEY_REMOVE_FAILED, STATUS_REDEEMED, STATUS_REVOKED)

DEFAULT_INVITE_TTL_HOURS = 72


def _member_or_404(member_id):
    member = db.session.get(FaroMember, member_id)
    if not member:
        return None, (jsonify({'error': 'member not found'}), 404)
    return member, None


def _device_or_404(device_id):
    device = db.session.get(FaroDevice, device_id)
    if not device:
        return None, (jsonify({'error': 'device not found'}), 404)
    return device, None


def _clean_text(value, limit):
    value = (value or '').strip()
    return value[:limit] or None


def _member_summary(member, now):
    devices = FaroDevice.query.filter_by(member_id=member.id).all()
    rows = FaroDeviceKey.query.filter_by(member_id=member.id).all()
    data = member.to_dict()
    data.update({
        'devices_active': sum(d.status == DEVICE_ACTIVE for d in devices),
        'devices_pending': sum(d.status == DEVICE_PENDING for d in devices),
        'grants_live': len(members.live_grants(member.id, now)),
        'keys_failing': sum(r.key_status in ('failed', KEY_REMOVE_FAILED) for r in rows),
        'last_device_at': max((d.to_dict()['created_at'] for d in devices), default=None),
    })
    return data


# --------------------------------------------------------------------------- #
# members
# --------------------------------------------------------------------------- #
@bp.route('/members', methods=['GET'])
@admin_required
def list_members():
    now = datetime.utcnow()
    out = [_member_summary(m, now) for m in FaroMember.query.order_by(FaroMember.name).all()]
    return jsonify({'members': out}), 200


@bp.route('/members', methods=['POST'])
@admin_required
def create_member():
    data = request.get_json(silent=True) or {}
    name = _clean_text(data.get('name'), 255)
    if not name:
        return jsonify({'error': 'name is required'}), 400
    member = FaroMember(name=name, email=_clean_text(data.get('email'), 255),
                        note=_clean_text(data.get('note'), 2000),
                        created_by=_current_user_id(), created_at=datetime.utcnow())
    db.session.add(member)
    db.session.commit()
    audit('faro.member.created', 'faro_member', member.id, details={'name': name})
    return jsonify({'member': _member_summary(member, datetime.utcnow())}), 201


@bp.route('/members/<int:member_id>', methods=['GET'])
@admin_required
def get_member(member_id):
    member, err = _member_or_404(member_id)
    if err:
        return err
    devices = FaroDevice.query.filter_by(member_id=member.id).order_by(FaroDevice.id).all()
    labels = {d.id: d.label for d in devices}
    keys_out = []
    for row in FaroDeviceKey.query.filter_by(member_id=member.id).order_by(FaroDeviceKey.id).all():
        row.device_label = labels.get(row.device_id)
        keys_out.append(row.to_dict())
    grants = (FaroGrant.query.filter_by(member_id=member.id)
              .order_by(FaroGrant.created_at.desc()).all())
    invites = (FaroInvite.query.filter_by(member_id=member.id)
               .order_by(FaroInvite.created_at.desc()).limit(20).all())
    return jsonify({
        'member': _member_summary(member, datetime.utcnow()),
        'devices': [d.to_dict() for d in devices],
        'grants': [_grant_json(g, member_names={member.id: member.name}) for g in grants],
        'invites': [i.to_dict() for i in invites],
        'keys': keys_out,
    }), 200


@bp.route('/members/<int:member_id>', methods=['PATCH'])
@admin_required
def update_member(member_id):
    member, err = _member_or_404(member_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'name' in data:
        name = _clean_text(data.get('name'), 255)
        if not name:
            return jsonify({'error': 'name is required'}), 400
        member.name = name
    if 'email' in data:
        member.email = _clean_text(data.get('email'), 255)
    if 'note' in data:
        member.note = _clean_text(data.get('note'), 2000)
    db.session.commit()
    return jsonify({'member': _member_summary(member, datetime.utcnow())}), 200


@bp.route('/members/<int:member_id>/revoke-all', methods=['POST'])
@admin_required
def revoke_member(member_id):
    """Cut a member off: revoke every live grant, device and open invite,
    and remove every key."""
    member, err = _member_or_404(member_id)
    if err:
        return err
    now = datetime.utcnow()
    for grant in FaroGrant.query.filter_by(member_id=member.id, status=STATUS_REDEEMED).all():
        grant.status, grant.revoked_at = STATUS_REVOKED, now
    for device in FaroDevice.query.filter(FaroDevice.member_id == member.id,
                                          FaroDevice.status.in_([DEVICE_ACTIVE, DEVICE_PENDING])).all():
        device.status, device.revoked_at = DEVICE_REVOKED, now
    for invite in FaroInvite.query.filter_by(member_id=member.id, status=INVITE_ACTIVE).all():
        invite.status = INVITE_REVOKED
    failed = members.reconcile(member.id, user_id=_current_user_id())
    db.session.commit()
    audit('faro.member.revoked', 'faro_member', member.id,
          details={'name': member.name, 'failures': _member_failures(failed)})
    return jsonify({'member': _member_summary(member, now),
                    'failed': _member_failures(failed)}), 200


@bp.route('/members/<int:member_id>', methods=['DELETE'])
@admin_required
def delete_member(member_id):
    """Delete a member and their history. Refused while they have live
    access, devices, or a key still on a server."""
    member, err = _member_or_404(member_id)
    if err:
        return err
    if members.live_grants(member.id):
        return jsonify({'error': 'revoke this member\'s access first'}), 409
    if FaroDevice.query.filter(FaroDevice.member_id == member.id,
                               FaroDevice.status.in_([DEVICE_ACTIVE, DEVICE_PENDING])).count():
        return jsonify({'error': 'revoke this member\'s devices first'}), 409
    if FaroDeviceKey.query.filter(FaroDeviceKey.member_id == member.id,
                                  FaroDeviceKey.key_status.in_([KEY_INSTALLED, KEY_REMOVE_FAILED])).count():
        return jsonify({'error': 'a key is still on a server; retry the cleanup first'}), 409
    for model in (FaroDeviceKey, FaroDevice, FaroInvite, FaroGrant):
        model.query.filter_by(member_id=member.id).delete(synchronize_session=False)
    db.session.delete(member)
    db.session.commit()
    audit('faro.member.deleted', 'faro_member', member_id, details={'name': member.name})
    return jsonify({'deleted': member_id}), 200


@bp.route('/members/<int:member_id>/sync', methods=['POST'])
@admin_required
def sync_member(member_id):
    """Retry everything outstanding for a member (failed installs/removals)."""
    member, err = _member_or_404(member_id)
    if err:
        return err
    failed = members.reconcile(member.id, user_id=_current_user_id())
    db.session.commit()
    return jsonify({'failed': _member_failures(failed)}), 200


# --------------------------------------------------------------------------- #
# invites
# --------------------------------------------------------------------------- #
@bp.route('/members/<int:member_id>/invites', methods=['POST'])
@admin_required
def create_invite(member_id):
    """Mint an "add a device" link for a member. Needs at least one live
    grant: the link's manifest lists what the device will reach."""
    member, err = _member_or_404(member_id)
    if err:
        return err
    if not members.live_grants(member.id):
        return jsonify({'error': f'give {member.name} access to at least one server first'}), 409
    data = request.get_json(silent=True) or {}
    try:
        ttl_hours = int(data.get('ttl_hours') or DEFAULT_INVITE_TTL_HOURS)
    except (TypeError, ValueError):
        return jsonify({'error': 'ttl_hours must be a whole number of hours'}), 400
    ttl_hours = max(1, min(MAX_TTL_HOURS, ttl_hours))
    require_approval = data.get('require_approval', True) is not False

    token = grants_lib.new_token()
    now = datetime.utcnow()
    invite = FaroInvite(member_id=member.id, token_hash=grants_lib.hash_token(token),
                        require_approval=require_approval, status=INVITE_ACTIVE,
                        created_by=_current_user_id(), created_at=now,
                        expires_at=now + timedelta(hours=ttl_hours))
    db.session.add(invite)
    db.session.commit()
    audit('faro.invite.created', 'faro_invite', invite.id, details={
        'member': member.name, 'ttl_hours': ttl_hours, 'require_approval': require_approval,
    })
    link = grants_lib.build_faro_link(_panel_base_url(), token, member.name)
    return jsonify({'invite': invite.to_dict(), 'link': link}), 201


@bp.route('/invites/<int:invite_id>/revoke', methods=['POST'])
@admin_required
def revoke_invite(invite_id):
    invite = db.session.get(FaroInvite, invite_id)
    if not invite:
        return jsonify({'error': 'invite not found'}), 404
    if invite.status == INVITE_ACTIVE:
        invite.status = INVITE_REVOKED
        db.session.commit()
        audit('faro.invite.revoked', 'faro_invite', invite.id, details={'member_id': invite.member_id})
    return jsonify({'invite': invite.to_dict()}), 200


# --------------------------------------------------------------------------- #
# devices
# --------------------------------------------------------------------------- #
def _device_action(device_id, allowed_from, new_status, event):
    device, err = _device_or_404(device_id)
    if err:
        return err
    if device.status not in allowed_from:
        return jsonify({'error': f'a {device.status} device cannot be changed this way'}), 409
    now = datetime.utcnow()
    device.status = new_status
    if new_status == DEVICE_ACTIVE:
        device.approved_at, device.approved_by = now, _current_user_id()
    else:
        device.revoked_at = now
    failed = members.reconcile(device.member_id, user_id=_current_user_id())
    db.session.commit()
    audit(event, 'faro_device', device.id, details={
        'member_id': device.member_id, 'label': device.label,
        'fingerprint': grants_lib.key_fingerprint(device.public_key),
        'failures': _member_failures(failed),
    })
    return jsonify({'device': device.to_dict(), 'failed': _member_failures(failed)}), 200


@bp.route('/devices/<int:device_id>/approve', methods=['POST'])
@admin_required
def approve_device(device_id):
    return _device_action(device_id, (DEVICE_PENDING,), DEVICE_ACTIVE, 'faro.device.approved')


@bp.route('/devices/<int:device_id>/reject', methods=['POST'])
@admin_required
def reject_device(device_id):
    return _device_action(device_id, (DEVICE_PENDING,), DEVICE_REJECTED, 'faro.device.rejected')


@bp.route('/devices/<int:device_id>/revoke', methods=['POST'])
@admin_required
def revoke_device(device_id):
    return _device_action(device_id, (DEVICE_ACTIVE,), DEVICE_REVOKED, 'faro.device.revoked')


@bp.route('/devices/<int:device_id>', methods=['PATCH'])
@admin_required
def rename_device(device_id):
    device, err = _device_or_404(device_id)
    if err:
        return err
    label = _clean_text((request.get_json(silent=True) or {}).get('label'), 255)
    if not label:
        return jsonify({'error': 'label is required'}), 400
    device.label = label
    db.session.commit()
    return jsonify({'device': device.to_dict()}), 200


# --------------------------------------------------------------------------- #
# public protocol side (called from grants.py's two public routes)
# --------------------------------------------------------------------------- #
def invite_for_token(token):
    """The invite this token belongs to, or None (then it's a grant token)."""
    return FaroInvite.query.filter_by(token_hash=grants_lib.hash_token(token)).first()


def _usable_invite_or_error(invite):
    if invite.status == INVITE_REVOKED:
        return jsonify({'error': 'grant revoked'}), 410
    if invite.status != INVITE_ACTIVE:
        return jsonify({'error': 'unknown grant'}), 404
    if invite.is_past_expiry():
        invite.status = INVITE_EXPIRED
        db.session.commit()
        return jsonify({'error': 'unknown grant'}), 404
    return None


def invite_manifest_response(invite, panel_url):
    err = _usable_invite_or_error(invite)
    if err:
        return err
    member = db.session.get(FaroMember, invite.member_id)
    grants = members.live_grants(invite.member_id)
    if not member or not grants:
        return jsonify({'error': 'nothing to share yet'}), 404
    return jsonify(members.invite_manifest(member, grants, panel_url)), 200


def invite_key_response(invite):
    """Step 2 for an invite: register the uploaded key as a new device.

    With approval required the device waits as pending and the reply says so
    (``pending_approval``); every connection is listed as installed so Faro
    imports them, and they start working once an admin approves. Without
    approval the key is installed now; if nothing at all could be installed
    the device is dropped and the invite stays usable (502).
    """
    err = _usable_invite_or_error(invite)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        key_line = grants_lib.validate_public_key(data.get('public_key'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    member = db.session.get(FaroMember, invite.member_id)
    grants = members.live_grants(invite.member_id)
    if not member or not grants:
        return jsonify({'error': 'nothing to share yet'}), 404

    material = ' '.join(key_line.split()[:2])
    for other in FaroDevice.query.filter_by(member_id=member.id).all():
        if ' '.join(other.public_key.split()[:2]) == material:
            return jsonify({'error': 'this key is already registered'}), 409

    now = datetime.utcnow()
    claimed = FaroInvite.query.filter_by(id=invite.id, status=INVITE_ACTIVE).update(
        {'status': INVITE_USED, 'used_at': now}, synchronize_session=False)
    db.session.commit()
    if claimed != 1:
        return jsonify({'error': 'unknown grant'}), 404
    db.session.refresh(invite)

    count = FaroDevice.query.filter_by(member_id=member.id).count()
    label = _clean_text(data.get('device_name'), 255) or f'Device {count + 1}'
    device = FaroDevice(member_id=member.id, invite_id=invite.id, label=label,
                        public_key=key_line, redeemed_ip=get_client_ip(), created_at=now,
                        status=DEVICE_PENDING if invite.require_approval else DEVICE_ACTIVE)
    if not invite.require_approval:
        device.approved_at = now
    db.session.add(device)
    db.session.flush()
    invite.device_id = device.id

    connections, needs = members.invite_connections(member, grants)
    labels = [c['name'] for c in connections[:64]]
    details = {'member': member.name, 'device': label, 'ip': device.redeemed_ip,
               'fingerprint': grants_lib.key_fingerprint(key_line)}

    if device.status == DEVICE_PENDING:
        db.session.commit()
        audit('faro.device.requested', 'faro_device', device.id, details=details,
              user_id=invite.created_by)
        return jsonify({'installed': labels, 'failed': [], 'pending_approval': True,
                        'message': 'Waiting for an administrator to approve this device.'}), 200

    failed_lines = members.reconcile(member.id, user_id=invite.created_by)
    installed_rows = {(r.server_id, r.username) for r in
                      FaroDeviceKey.query.filter_by(device_id=device.id,
                                                    key_status=KEY_INSTALLED).all()}
    installed = [lbl for lbl in labels if all(line in installed_rows for line in needs[lbl])]
    if not installed:
        # Nothing works: undo the device so the invite can be used again.
        device.status = DEVICE_REVOKED
        members.reconcile(member.id, user_id=invite.created_by)
        leftover = FaroDeviceKey.query.filter_by(device_id=device.id, key_status=KEY_REMOVE_FAILED).count()
        if not leftover:
            FaroDeviceKey.query.filter_by(device_id=device.id).delete(synchronize_session=False)
            db.session.delete(device)
            invite.status, invite.used_at, invite.device_id = INVITE_ACTIVE, None, None
        db.session.commit()
        audit('faro.device.install_failed', 'faro_invite', invite.id,
              details={**details, 'failed': _member_failures(failed_lines)}, user_id=invite.created_by)
        return jsonify({'error': 'key install failed on every server',
                        'failed': _member_failures(failed_lines)}), 502

    db.session.commit()
    audit('faro.device.added', 'faro_device', device.id, details=details, user_id=invite.created_by)
    failed = [{'name': lbl, 'error': 'key install failed on this server'}
              for lbl in labels if lbl not in installed]
    return jsonify({'installed': installed, 'failed': failed}), 200
