# serverkit-faro

**Open in Faro** — a one-click "Connect with Faro" button on your ServerKit
Services, Domains, and WordPress sites. Clicking it opens
[Faro](https://github.com/jhd3197/faro) (the desktop SFTP/SSH client) with its
**New Connection editor prefilled** for that site — a magnet link for a server.

This is an **independent** ServerKit extension: it lives in its own repo and is
released on its own cadence, listed in the
[`serverkit-extensions`](https://github.com/jhd3197/serverkit-extensions)
registry. Installing it needs no ServerKit release.

## What it does

- Adds a small **Faro** panel to:
  - **Service detail** pages (`service.detail.tab` slot)
  - **Domain** drawers (`domain.drawer.panel` slot)
  - **WordPress** site pages (`wordpress.detail.panel` slot — requires a panel
    build that ships this slot; on older panels the button simply doesn't appear
    there, while Services and Domains still work)
- Each panel resolves the site's server address and web root and emits a
  `faro://connect?protocol=sftp&host=…&path=…&name=…` deep link.

## Security model

A `faro://` link is treated as untrusted input by Faro and by this extension:

- **Never connects on its own** — it only *prefills* Faro's editor; the user
  reviews and clicks Connect.
- **Never carries a password/secret/passphrase** — those params are never
  emitted here and are dropped by Faro anyway. The username/port are optional
  (Faro defaults the port; the user types the username once).

See [`Faro/docs/deep-links.md`](https://github.com/jhd3197/faro/blob/main/docs/deep-links.md)
for the full `faro://` contract.

## Access grants

The extension is also a **Faro Grant Protocol v1 issuer**: give someone
time-boxed SSH/SFTP access to **one or many managed servers with a single
link**. Access is key-based, single-use and revocable, with an optional
**bridge** (a managed jump server) so every person connects from the same
static address. No password and none of the owner's keys ever change hands:
Faro generates a fresh ed25519 keypair on the *person's* machine after they
click Accept and uploads only the public key. The extension installs that key
for the grant's login on each granted server (and the bridge) through the
ServerKit agent. Revoking or expiring the grant removes exactly that line.

### In the panel

- **Access** page (`/faro`, in the sidebar): every grant with its status
  (Waiting → Live → Revoked/Expired), servers, login, bridge and end time.
  **New access** picks the servers, duration (4h to 30 days), SSH login, start
  folder and bridge, then shows the `faro://grant` link **once** to copy.
- **Grant page** (`/faro/grants/:id`): where the key actually is, per server
  (Installed / Install failed / Removal failed, with the agent's error),
  whether sshd itself enforces the expiry, who accepted and from which IP, and
  the key fingerprint. Actions: **Retry**, **Extend**, **Revoke**, and
  **Delete record** once no key is left anywhere.
- **Share access…** on the Faro panel of a service, domain or WordPress site
  opens the form with that site's server already picked (admins only).

### What gets installed

Each key line is tagged `faro-grant:<id>` and restricted:

| Where | Options |
|---|---|
| Granted servers | `no-agent-forwarding,no-X11-forwarding`, plus `from="<bridge>"` when "only accept the key from the bridge" is on |
| Bridge | `no-pty,no-agent-forwarding,no-X11-forwarding,permitopen="<server>:22"` for each granted server |
| Both, on OpenSSH ≥ 8.2 | `expiry-time="…"`, so sshd refuses the key at the end time even if the panel never gets to remove it |

Redemption claims the token atomically. If the bridge or every granted server
fails, the keys that did land are removed and the link stays usable. If only
some servers fail, access goes live on the others and the grant page offers a
retry. Removals that fail (an offline agent, say) stay visible as "Key still on
a server" and are retried every 5 minutes for a day.

### API

The two public protocol endpoints (unauthenticated: the link's redemption
token is the auth; per-IP rate-limited):

```
GET  {panel}/api/v1/faro-grants/.well-known/faro-grant/{token}      → grant manifest
POST {panel}/api/v1/faro-grants/.well-known/faro-grant/{token}/key  → install {public_key}
```

Admin routes (admin JWT or `X-API-Key`):

| Route | |
|---|---|
| `GET /servers` | managed servers with `online` and `ssh_keys` (can the agent manage keys) |
| `POST /grants` | issue; returns `{grant, link}` |
| `GET /grants`, `GET /grants/<id>` | grants with per-server `targets` and a `health` flag (`ok` / `partial` / `cleanup`) |
| `POST /grants/<id>/extend` | `{ttl_hours}` from now; re-installs live keys with the new expiry |
| `POST /grants/<id>/retry` | retry failed installs (live grant) or failed removals (ended grant) |
| `POST /grants/<id>/revoke` | revoke and remove the key; calling it again retries removal |
| `DELETE /grants/<id>` | delete an ended grant once no key is left |

```bash
curl -X POST https://panel.example.com/api/v1/faro-grants/grants \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Client X — 2 servers",
        "server_ids": ["<server-uuid>", "<server-uuid>"],
        "ttl_hours": 72,
        "ssh_username": "deploy",
        "create_user": true,
        "path": "/var/www",
        "jump": {"server_id": "<bridge-server-uuid>", "username": "faro-grant"},
        "restrict_source": true
      }'
# → {"grant": {...}, "link": "faro://grant?issuer=…&token=…&name=…"}
```

`ttl_hours` defaults to 72 and is capped at 720. `jump`, `path`,
`ssh_username` (default `root`), `create_user` and `restrict_source` are
optional. The bridge must be a managed server, so its key can be installed and
removed with the grant.

**Limits:**

- One login per grant (every server in the bundle uses it). With
  `create_user`, a missing user is created with `useradd -m`.
- The link contains the redemption token. Treat it like a password until it's
  redeemed. Tokens are stored only as sha256, are single-use, and die at expiry.
- Keys go in the login's `~/.ssh/authorized_keys`. Servers using an
  `AuthorizedKeysFile` override or centralized key management need manual
  handling.

Full protocol spec:
[`Faro/docs/grant-links.md`](https://github.com/jhd3197/Faro/blob/main/docs/grant-links.md).

## Requirements

- **ServerKit panel** ≥ 1.11.17 (SDK 1.5: extension pages, walkthroughs).
  Full WordPress support needs a panel build that ships the
  `wordpress.detail.panel` slot.
- **Faro** desktop app installed on the machine opening the links, with the
  `faro://` scheme registered (Faro's installer does this; dev builds register
  it at startup).
- **For access grants**, on every granted server and bridge:
  - a ServerKit agent new enough to report the `ssh.keys` capability, running
    as root or with passwordless sudo, on Linux;
  - agent permissions that allow `ssh:keys:*`: the default (Full Access), or
    the **SSH Access** profile (`permission_profile: "ssh_access"`) on a
    locked-down server.

  The form greys out servers that don't qualify and says why.

## Frontend + optional backend

The deep-link feature is pure frontend: it only generates links in the browser
from data the panel already exposes (`/apps/:id`, `/servers/:id`,
`/wordpress/sites/:id`). The access-grant feature adds a small Flask backend
(`backend/`, hot-loaded as `app.plugins.serverkit-faro`): the
`ext_serverkit_faro_grants` and `ext_serverkit_faro_grant_targets` tables, the
endpoints above, and a 5-minute sweeper job that expires grants, removes their
keys and retries failed removals. The Access pages are in the same frontend
bundle.

```bash
python -m unittest discover -s backend/tests -v   # protocol helpers, stdlib only
# grant lifecycle against a real panel + fake agents (needs ../ServerKit/backend
# or SERVERKIT_BACKEND, run with the panel's Python environment):
python -m unittest discover -s tests -v
```

## Build (local)

```bash
cd frontend
npm install
npm run build          # → frontend/dist/index.mjs (CSS inlined)
npm test               # unit-tests the faro:// link builder

# from the repo root — stage + zip the release artifact
./scripts/build-zip.sh # → dist/serverkit-faro-<version>.zip
```

## Releasing

Releases are automated by GitHub Actions — no manual zip building or uploading.
`plugin.json`'s `version` is the single source of truth.

- **Stable release** — bump `version` in `plugin.json`, open a PR from `dev`
  → `main`, and merge it. The [`Create Release`](.github/workflows/release.yml)
  workflow reads the new version, builds `frontend/dist/index.mjs`, zips the
  artifact, tags `v<version>`, and publishes a GitHub Release with the zip and
  its `sha256` attached. (Merging without bumping the version is a no-op — the
  existing tag is detected and the release is skipped.)
- **Beta release** — push to `dev` (or run the
  [`Create Beta Release`](.github/workflows/beta-release.yml) workflow manually).
  It auto-increments a `v<version>-beta.N` **prerelease** off the current
  `plugin.json` base version.
- **CI** — every PR to `main`/`dev` runs
  [`CI`](.github/workflows/ci.yml): `npm ci && npm run build && npm test` plus a
  zip-build smoke check.

After a stable release, add/update the entry in
[`serverkit-extensions`](https://github.com/jhd3197/serverkit-extensions)
`index.json` (`bundled: false`, `source` pointing at the release asset, with the
release's recorded `sha256`).

## License

MIT © Juan Denis
