"""Data model for the serverkit-faro (Faro) extension backend.

One table, namespaced ``ext_serverkit_faro_*`` (dash -> underscore) so
``--purge`` on uninstall drops exactly this:

* :class:`FaroGrant` — one issued access grant: the hashed redemption token,
  the target server bundle, the SSH username/path/jump shape, the installed
  public key (kept so revoke/expiry can remove that exact line), and the
  lifecycle status (active -> redeemed | revoked | expired).

Registration mirrors the serverkit-analytics pattern: importing this module
defines the table on the shared metadata as a side effect; the manifest's
``models: "models:register"`` then calls :func:`register` (a passthrough) and
the platform runs ``db.create_all()``.

The raw grant token is NEVER stored — only its sha256 hex digest
(:func:`grants_lib.hash_token`), and ``to_dict`` never includes the digest.
"""
import json
from datetime import datetime

from app import db

#: Lifecycle states. 'active' is redeemable; 'redeemed' has a key installed;
#: 'revoked' was killed by an admin; 'expired' passed its TTL (lazy-marked by
#: the public endpoints, swept by the faro.expire_grants job).
STATUS_ACTIVE = 'active'
STATUS_REDEEMED = 'redeemed'
STATUS_REVOKED = 'revoked'
STATUS_EXPIRED = 'expired'


class FaroGrant(db.Model):
    __tablename__ = 'ext_serverkit_faro_grants'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    # sha256 hex of the redemption token. Unique + indexed for the public
    # endpoints' hot-path lookup. The raw token only ever lives in the link.
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # One username per grant (v1): the login the uploaded key is installed for.
    ssh_username = db.Column(db.String(64), nullable=False, default='root')
    # Optional default remote path for every connection in the bundle.
    default_path = db.Column(db.String(512), nullable=True)

    # JSON list of {"server_id", "name", "host"} snapshots taken at issue
    # time (a server renamed/removed later must not break redemption).
    servers_json = db.Column(db.Text, nullable=False, default='[]')
    # Optional JSON {"host", "port", "username"} bastion hop, same for every
    # connection (this is how every grantee gets the same static source IP).
    jump_json = db.Column(db.Text, nullable=True)

    # The installed public-key line (public material, not a secret). Kept so
    # revoke/expiry can remove exactly this line from each authorized_keys.
    public_key = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(16), nullable=False, default=STATUS_ACTIVE,
                       index=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)

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

    def is_past_expiry(self, now=None):
        return bool(self.expires_at) and (now or datetime.utcnow()) >= self.expires_at

    def to_dict(self):
        """Admin-facing shape. NEVER includes token_hash."""
        return {
            'id': self.id,
            'name': self.name,
            'ssh_username': self.ssh_username,
            'default_path': self.default_path,
            'servers': self.server_list(),
            'jump': self.jump(),
            'public_key': self.public_key,
            'status': self.status,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'redeemed_at': self.redeemed_at.isoformat() if self.redeemed_at else None,
        }


def register(db):  # noqa: A002 - signature dictated by the platform (fn(db))
    """Passthrough required by the manifest ``models: "models:register"``.

    Importing this module already defined the table on ``db.metadata``; the
    platform runs ``db.create_all()``. Returning the classes is convenient for
    tests.
    """
    return [FaroGrant]
