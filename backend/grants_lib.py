"""Pure grant-protocol helpers for the serverkit-faro extension.

This module deliberately imports NOTHING from ``app.*`` (or Flask) so it can be
unit-tested standalone with ``python -m unittest discover -s backend/tests``
outside the panel process. Everything here is deterministic string/dict work:

* token minting + hashing (the raw token is only ever held by the caller;
  the database stores the sha256 hex digest)
* SSH public-key line validation/normalization
* Faro Grant Protocol v1 manifest construction (snake_case, EXACTLY as
  specified in Faro's ``docs/grant-links.md`` — the Faro Rust client has a
  test parsing the spec verbatim)
* ``faro://grant`` link construction
* idempotent ``authorized_keys`` line add/remove helpers
"""
import hashlib
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse

#: Accepted SSH public key algorithms (Faro generates ed25519; rsa/ecdsa are
#: accepted so manually-issued keys work too).
_KEY_TYPES = (
    'ssh-ed25519',
    'ssh-rsa',
    'ecdsa-sha2-nistp256',
    'ecdsa-sha2-nistp384',
    'ecdsa-sha2-nistp521',
)

#: A base64-shaped key blob (standard alphabet, optional padding).
_BLOB_RE = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')

#: Sane blob length bounds. A real ed25519 blob is 68 base64 chars; a 4096-bit
#: RSA blob is ~736. Anything outside this window is not a plausible key.
_BLOB_MIN_LEN = 40
_BLOB_MAX_LEN = 2048


def new_token():
    """Mint an opaque redemption token (>= 128 bits, urlsafe charset).

    The spec requires ``[A-Za-z0-9_-]{16,128}``; ``token_urlsafe(24)`` yields
    32 such characters from 24 random bytes (192 bits).
    """
    return secrets.token_urlsafe(24)


def hash_token(token):
    """SHA-256 hex digest of a token. The DB stores ONLY this."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def validate_public_key(line):
    """Validate + normalize one OpenSSH public-key line.

    Returns the normalized line (whitespace collapsed, optional comment kept).
    Raises ``ValueError`` for anything that is not a single-line
    ``<type> <base64-blob> [comment]`` with an accepted algorithm.
    """
    if not isinstance(line, str):
        raise ValueError('public key must be a string')
    line = line.strip()
    if not line or '\n' in line or '\r' in line:
        raise ValueError('public key must be a single line')
    parts = line.split()
    if len(parts) < 2:
        raise ValueError('public key must be "<type> <base64-blob> [comment]"')
    key_type, blob = parts[0], parts[1]
    if key_type not in _KEY_TYPES:
        raise ValueError(f'unsupported key type: {key_type}')
    if not _BLOB_RE.match(blob):
        raise ValueError('key blob is not valid base64')
    if not (_BLOB_MIN_LEN <= len(blob) <= _BLOB_MAX_LEN):
        raise ValueError('key blob has an implausible length')
    return ' '.join(parts)


def build_manifest(*, issuer_name, grant, servers, panel_url):
    """Build the Faro Grant Protocol v1 manifest dict (snake_case, per spec).

    ``grant`` is a plain dict with keys ``name`` (required), ``expires_at``
    (datetime or None), ``ssh_username``, ``path`` and ``jump`` (dict with
    ``host``/``port``/``username`` or None). ``servers`` is a list of dicts
    with ``name`` and ``host``. ``panel_url`` may be a full URL or a bare
    host; only the host part lands in the display ``issuer`` field.
    """
    host = _host_of(panel_url)
    name = grant['name']
    expires_at = grant.get('expires_at')
    username = grant.get('ssh_username') or 'root'
    path = grant.get('path')
    jump = grant.get('jump')

    connections = []
    for server in servers:
        conn = {
            'name': server.get('name'),
            'protocol': 'sftp',
            'host': server['host'],
            'port': 22,
            'username': username,
        }
        if path:
            conn['path'] = path
        if jump:
            # The SAME key authenticates both hops; the jump username defaults
            # to the grant username when not given explicitly.
            conn['jump'] = {
                'host': jump['host'],
                'port': int(jump.get('port') or 22),
                'username': jump.get('username') or username,
            }
        connections.append(conn)

    # Spec field order: version, issuer, name, group, expires_at, auth,
    # connections (dicts preserve insertion order).
    manifest = {
        'version': 1,
        'issuer': f'{issuer_name} · {host}',
        'name': name,
        'group': grant.get('group') or name,
    }
    if expires_at is not None:
        # RFC 3339, UTC, trailing Z (informational — enforcement is ours).
        manifest['expires_at'] = _rfc3339(expires_at)
    manifest['auth'] = {'type': 'key-install'}
    manifest['connections'] = connections
    return manifest


def build_faro_link(panel_url, token, name):
    """Build the shareable ``faro://grant`` link for a freshly-minted token.

    ``issuer`` points at this extension's url_prefix — the two public
    protocol endpoints live directly under it. Everything is url-encoded
    (spaces as ``%20``, matching the spec's example).
    """
    issuer = f'{panel_url.rstrip("/")}/api/v1/faro-grants'
    query = urlencode({'issuer': issuer, 'token': token, 'name': name},
                      quote_via=quote)
    return f'faro://grant?{query}'


def authorized_keys_add(content, key_line):
    """Return ``content`` with ``key_line`` appended, idempotently.

    Matching is on the key material (type + blob), so a line whose comment
    differs still counts as present. Existing content is preserved verbatim.
    """
    key_line = key_line.strip()
    target = key_line.split()[:2]
    base = content or ''
    for line in base.splitlines():
        if line.strip() and line.split()[:2] == target:
            return base
    if base and not base.endswith('\n'):
        base += '\n'
    return base + key_line + '\n'


def authorized_keys_remove(content, key_line):
    """Return ``content`` with every occurrence of the key removed.

    Matching is on the key material (type + blob), so comment edits by an
    admin don't defeat revocation. A no-op returns the content unchanged.
    """
    if not content:
        return ''
    target = key_line.strip().split()[:2]
    lines = content.splitlines()
    kept = [line for line in lines
            if not (line.strip() and line.split()[:2] == target)]
    if len(kept) == len(lines):
        return content
    out = '\n'.join(kept)
    return (out + '\n') if out.strip() else ''


def _host_of(panel_url):
    """Extract the host (with port) from a URL, or pass a bare host through."""
    text = (panel_url or '').strip()
    if '://' in text:
        return urlparse(text).netloc or text
    return text


def _rfc3339(dt):
    """Format a datetime as RFC 3339 UTC with a trailing Z."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
