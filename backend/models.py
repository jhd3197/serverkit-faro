"""Data model for the serverkit-faro (Faro) extension backend.

Tables, namespaced ``ext_serverkit_faro_*`` (dash -> underscore) so
``--purge`` on uninstall drops exactly these:

* :class:`FaroGrant` — one issued access grant: the hashed redemption token,
  the target server bundle, the SSH username/path/jump shape, the installed
  public key (kept so revoke/expiry can remove that exact line), and the
  lifecycle status (active -> redeemed | revoked | expired).
* :class:`FaroGrantTarget` — one row per server a one-time grant's key goes
  on (every target, plus the bridge/jump server when there is one), with that
  server's key state. This is what makes partial installs retryable and what
  keeps a failed removal visible (and retried by the sweeper) instead of
  silently leaving a key behind on a grant that reads "revoked".
* :class:`FaroMember` — a person. Grants can belong to a member instead of
  carrying their own one-time link; the member's devices then get access.
* :class:`FaroDevice` — one key a member uploaded from Faro (a laptop, say),
  optionally waiting for an admin's approval before it is installed anywhere.
* :class:`FaroInvite` — a single-use "add a device" link for a member.
* :class:`FaroDeviceKey` — one device key on one server for one login: the
  reconciled, per-line install state for member access (see :mod:`members`).

Registration mirrors the serverkit-analytics pattern: importing this module
defines the tables on the shared metadata as a side effect; the manifest's
``models: "models:register"`` then calls :func:`register`, which also adds any
columns newer releases introduced to an already-existing grants table (the
platform's ``db.create_all()`` only creates missing tables, never columns).

The raw grant token is NEVER stored — only its sha256 hex digest
(:func:`grants_lib.hash_token`), and ``to_dict`` never includes the digest.
"""
import json
from datetime import datetime

from app import db

from . import grants_lib

#: Lifecycle states. 'active' is redeemable; 'redeemed' has a key installed;
#: 'revoked' was killed by an admin; 'expired' passed its TTL (lazy-marked by
#: the public endpoints, swept by the faro.expire_grants job).
STATUS_ACTIVE = 'active'
STATUS_REDEEMED = 'redeemed'
STATUS_REVOKED = 'revoked'
STATUS_EXPIRED = 'expired'

FINISHED_STATUSES = (STATUS_REVOKED, STATUS_EXPIRED)

#: Per-server key states. pending -> installed | failed on redeem;
#: installed -> removed | remove_failed on revoke/expiry.
KEY_PENDING = 'pending'
KEY_INSTALLED = 'installed'
KEY_FAILED = 'failed'
KEY_REMOVED = 'removed'
KEY_REMOVE_FAILED = 'remove_failed'

ROLE_TARGET = 'target'
ROLE_JUMP = 'jump'


class FaroGrant(db.Model):
    __tablename__ = 'ext_serverkit_faro_grants'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    # sha256 hex of the redemption token. Unique + indexed for the public
    # endpoints' hot-path lookup. The raw token only ever lives in the link.
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # Owning member (added in 1.2). NULL for a one-time link grant, which
    # carries its own token and key; a member grant has neither and gives
    # access to the member's devices instead.
    member_id = db.Column(db.Integer, nullable=True, index=True)

    # One username per grant (v1): the login the uploaded key is installed for.
    ssh_username = db.Column(db.String(64), nullable=False, default='root')
    # Optional default remote path for every connection in the bundle.
    default_path = db.Column(db.String(512), nullable=True)

    # JSON list of {"server_id", "name", "host"} snapshots taken at issue
    # time (a server renamed/removed later must not break redemption).
    servers_json = db.Column(db.Text, nullable=False, default='[]')
    # Optional JSON {"server_id", "name", "host", "port", "username"} bastion
    # hop, same for every connection (this is how every grantee gets the same
    # static source IP). Grants issued before 1.1 may lack server_id.
    jump_json = db.Column(db.Text, nullable=True)
    # JSON options: {"restrict_source": bool, "create_user": bool}. Added in
    # 1.1 (see _ADDED_COLUMNS).
    options_json = db.Column(db.Text, nullable=True)

    # The installed public-key line (public material, not a secret). Kept so
    # revoke/expiry can remove exactly this line from each authorized_keys.
    public_key = db.Column(db.Text, nullable=True)
    # Client IP that redeemed the link (added in 1.1).
    redeemed_ip = db.Column(db.String(64), nullable=True)

    status = db.Column(db.String(16), nullable=False, default=STATUS_ACTIVE,
                       index=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    # ---- helpers ----
    def server_list(self):
        """The granted servers as a list of {"server_id", "name", "host"}."""
        try:
            return json.loads(self.servers_json or '[]')
        except (ValueError, TypeError):
            return []

    def set_servers(self, servers):
        self.servers_json = json.dumps(servers or [])

    def jump(self):
        """The optional bastion hop as a dict, or None."""
        if not self.jump_json:
            return None
        try:
            return json.loads(self.jump_json)
        except (ValueError, TypeError):
            return None

    def set_jump(self, jump):
        self.jump_json = json.dumps(jump) if jump else None

    def options(self):
        try:
            return json.loads(self.options_json or '{}') or {}
        except (ValueError, TypeError):
            return {}

    def set_options(self, options):
        self.options_json = json.dumps(options or {})

    def is_past_expiry(self, now=None):
        return bool(self.expires_at) and (now or datetime.utcnow()) >= self.expires_at

    def to_dict(self, targets=None):
        """Admin-facing shape. NEVER includes token_hash.

        Pass ``targets`` (this grant's FaroGrantTarget rows) to include the
        per-server key state and the derived ``health`` flag.
        """
        options = self.options()
        data = {
            'id': self.id,
            'name': self.name,
            'member_id': self.member_id,
            'ssh_username': self.ssh_username,
            'default_path': self.default_path,
            'servers': self.server_list(),
            'jump': self.jump(),
            'restrict_source': bool(options.get('restrict_source')),
            'create_user': bool(options.get('create_user')),
            'public_key': self.public_key,
            'key_fingerprint': grants_lib.key_fingerprint(self.public_key),
            'redeemed_ip': self.redeemed_ip,
            'status': self.status,
            'created_by': self.created_by,
            'created_at': _iso(self.created_at),
            'expires_at': _iso(self.expires_at),
            'redeemed_at': _iso(self.redeemed_at),
            'revoked_at': _iso(self.revoked_at),
        }
        if targets is not None:
            data['targets'] = [t.to_dict() for t in targets]
            data['health'] = grant_health(self, targets)
        return data


class FaroGrantTarget(db.Model):
    __tablename__ = 'ext_serverkit_faro_grant_targets'

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, nullable=False, index=True)
    server_id = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    host = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(16), nullable=False, default=ROLE_TARGET)
    key_status = db.Column(db.String(16), nullable=False, default=KEY_PENDING)
    error = db.Column(db.Text, nullable=True)
    # Whether sshd on this server will itself refuse the key at expiry
    # (expiry-time= needs OpenSSH >= 8.2; the agent decides).
    expiry_enforced = db.Column(db.Boolean, nullable=False, default=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    installed_at = db.Column(db.DateTime, nullable=True)
    removed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'name': self.name,
            'host': self.host,
            'role': self.role,
            'key_status': self.key_status,
            'error': self.error,
            'expiry_enforced': bool(self.expiry_enforced),
            'attempts': self.attempts or 0,
            'installed_at': _iso(self.installed_at),
            'removed_at': _iso(self.removed_at),
            'updated_at': _iso(self.updated_at),
        }


#: Device / invite states.
DEVICE_PENDING = 'pending'      # uploaded, waiting for an admin's approval
DEVICE_ACTIVE = 'active'
DEVICE_REJECTED = 'rejected'
DEVICE_REVOKED = 'revoked'

INVITE_ACTIVE = 'active'
INVITE_USED = 'used'
INVITE_REVOKED = 'revoked'
INVITE_EXPIRED = 'expired'


class FaroMember(db.Model):
    __tablename__ = 'ext_serverkit_faro_members'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'note': self.note,
            'created_by': self.created_by,
            'created_at': _iso(self.created_at),
        }


class FaroDevice(db.Model):
    __tablename__ = 'ext_serverkit_faro_devices'

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, nullable=False, index=True)
    invite_id = db.Column(db.Integer, nullable=True)
    label = db.Column(db.String(255), nullable=False)
    public_key = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(16), nullable=False, default=DEVICE_PENDING, index=True)
    redeemed_ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.Integer, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'member_id': self.member_id,
            'label': self.label,
            'status': self.status,
            'fingerprint': grants_lib.key_fingerprint(self.public_key),
            'key_type': (self.public_key or '').split(' ')[0] or None,
            'redeemed_ip': self.redeemed_ip,
            'created_at': _iso(self.created_at),
            'approved_at': _iso(self.approved_at),
            'approved_by': self.approved_by,
            'revoked_at': _iso(self.revoked_at),
        }


class FaroInvite(db.Model):
    __tablename__ = 'ext_serverkit_faro_invites'

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, nullable=False, index=True)
    # sha256 of the redemption token, same scheme as grants.
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    require_approval = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(16), nullable=False, default=INVITE_ACTIVE, index=True)
    device_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    def is_past_expiry(self, now=None):
        return (now or datetime.utcnow()) >= self.expires_at

    def to_dict(self):
        return {
            'id': self.id,
            'member_id': self.member_id,
            'require_approval': bool(self.require_approval),
            'status': self.status,
            'device_id': self.device_id,
            'created_by': self.created_by,
            'created_at': _iso(self.created_at),
            'expires_at': _iso(self.expires_at),
            'used_at': _iso(self.used_at),
        }


class FaroDeviceKey(db.Model):
    """One device key on one server for one login — the unit the agent
    actually installs. Several member grants can want the same line (same
    server and login, or a shared bridge); reconciliation merges them into one
    row, so revoking one grant never pulls a key another grant still needs."""
    __tablename__ = 'ext_serverkit_faro_device_keys'
    __table_args__ = (
        db.UniqueConstraint('device_id', 'server_id', 'username',
                            name='uq_ext_serverkit_faro_device_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, nullable=False, index=True)
    device_id = db.Column(db.Integer, nullable=False, index=True)
    server_id = db.Column(db.String(64), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    host = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(16), nullable=False, default=ROLE_TARGET)
    key_status = db.Column(db.String(16), nullable=False, default=KEY_PENDING)
    error = db.Column(db.Text, nullable=True)
    # What was last installed, so reconciliation only calls the agent again
    # when the options or expiry actually change.
    options_json = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    expiry_enforced = db.Column(db.Boolean, nullable=False, default=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    installed_at = db.Column(db.DateTime, nullable=True)
    removed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'member_id': self.member_id,
            'device_id': self.device_id,
            'server_id': self.server_id,
            'username': self.username,
            'name': self.name,
            'host': self.host,
            'role': self.role,
            'key_status': self.key_status,
            'error': self.error,
            'expiry_enforced': bool(self.expiry_enforced),
            'attempts': self.attempts or 0,
            'expires_at': _iso(self.expires_at),
            'installed_at': _iso(self.installed_at),
            'removed_at': _iso(self.removed_at),
            'updated_at': _iso(self.updated_at),
            # Set by members.grant_rows for display; not a column.
            'device_label': getattr(self, 'device_label', None),
        }


def grant_health(grant, targets):
    """One word for the list view, on top of ``status``:

    * ``ok`` — nothing to do
    * ``partial`` — redeemed, but some servers failed to get the key
    * ``cleanup`` — revoked/expired, but a key is still on some server
    """
    statuses = {t.key_status for t in targets}
    if grant.status in FINISHED_STATUSES:
        if statuses & {KEY_INSTALLED, KEY_REMOVE_FAILED}:
            return 'cleanup'
        return 'ok'
    if grant.status == STATUS_REDEEMED and statuses & {KEY_FAILED, KEY_PENDING}:
        return 'partial'
    return 'ok'


def _iso(value):
    """Naive-UTC datetime -> RFC 3339 with Z, so browsers don't read it as
    local time."""
    return value.isoformat() + 'Z' if value else None


#: Columns added to an existing grants table by later releases:
#: (column, DDL type). Applied by register() before create_all().
_ADDED_COLUMNS = (
    ('options_json', 'TEXT'),
    ('redeemed_ip', 'VARCHAR(64)'),
    ('revoked_at', 'DATETIME'),
    ('member_id', 'INTEGER'),
)


def _ensure_columns(db):  # noqa: A002
    """Add any _ADDED_COLUMNS missing from an existing grants table."""
    from sqlalchemy import inspect, text
    table = FaroGrant.__tablename__
    inspector = inspect(db.engine)
    if table not in inspector.get_table_names():
        return  # create_all() builds it with every column
    have = {c['name'] for c in inspector.get_columns(table)}
    added = False
    for name, ddl in _ADDED_COLUMNS:
        if name not in have:
            db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
            added = True
    if added:
        db.session.commit()


def register(db):  # noqa: A002 - signature dictated by the platform (fn(db))
    """Hook for the manifest ``models: "models:register"``.

    Importing this module already defined the tables on ``db.metadata``; the
    platform runs ``db.create_all()`` right after. Returning the classes is
    convenient for tests.
    """
    _ensure_columns(db)
    return [FaroGrant, FaroGrantTarget, FaroMember, FaroDevice, FaroInvite,
            FaroDeviceKey]
