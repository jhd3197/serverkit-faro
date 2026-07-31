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

The backend also makes this panel a **Faro Grant Protocol v1 issuer**: hand a
developer time-boxed SSH/SFTP access to **one or many managed servers with a
single link** — key-based, single-use, revocable, with an optional bastion hop
so every grantee connects from the same static source IP. No password and none
of the owner's keys ever change hands: Faro generates a fresh ed25519 keypair
on the *user's* machine after they click Accept and uploads only the public
key, which this extension appends to the grant username's `authorized_keys` on
each granted server (via the ServerKit agent). Revoking or expiring the grant
removes exactly that line.

The two public protocol endpoints (unauthenticated — the link's redemption
token is the auth; per-IP rate-limited):

```
GET  {panel}/api/v1/faro-grants/.well-known/faro-grant/{token}      → grant manifest
POST {panel}/api/v1/faro-grants/.well-known/faro-grant/{token}/key  → install {public_key}
```

Issuing a grant (admin JWT or `X-API-Key`; every server must have an online
agent, since key install goes through it):

```bash
curl -X POST https://panel.example.com/api/v1/faro-grants/grants \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Client X — 2 servers",
        "server_ids": [1, 2],
        "ttl_hours": 72,
        "ssh_username": "root",
        "path": "/var/www",
        "jump": {"host": "bastion.example.com", "port": 22, "username": "faro-grant"}
      }'
# → {"grant": {...}, "link": "faro://grant?issuer=…&token=…&name=…"}
```

The reply's `link` is what you send to the developer. `ttl_hours` defaults to
72 and is capped at 720; `jump`, `path`, and `ssh_username` are optional.
`GET /grants` lists grants and `POST /grants/<id>/revoke` revokes one
(best-effort key removal; per-server failures are reported, not fatal).

**Caveats (v1):**

- The grant username must be `root` **or a pre-provisioned user whose
  `~/.ssh` already exists with correct ownership/permissions**. The agent's
  `file:write` can't `chown`/`chmod`, so for a non-root user a newly created
  `authorized_keys` would be root-owned and sshd `StrictModes` will reject it.
- One username per grant (every server in the bundle uses it).
- The link contains the redemption token — treat it like a password until
  it's redeemed. Tokens are stored only as sha256, are single-use, and die at
  expiry.
- Key install targets plain `~/.ssh/authorized_keys`; servers using
  `AuthorizedKeysFile` overrides or centralized key management need manual
  handling.

Full protocol spec:
[`Faro/docs/grant-links.md`](https://github.com/jhd3197/Faro/blob/main/docs/grant-links.md).

## Requirements

- **ServerKit panel** ≥ 1.7.0 (runtime-ESM extension loader). Full WordPress
  support needs a panel build that ships the `wordpress.detail.panel` slot.
- **Faro** desktop app installed on the machine viewing the panel, with the
  `faro://` scheme registered (Faro's installer does this; dev builds register
  it at startup).

## Frontend + optional backend

The deep-link feature is pure frontend: it only generates links in the browser
from data the panel already exposes (`/apps/:id`, `/servers/:id`,
`/wordpress/sites/:id`). The access-grant feature adds a small Flask backend
(`backend/`, hot-loaded as `app.plugins.serverkit-faro`): one
`ext_serverkit_faro_grants` table, the two public protocol endpoints above,
and a 5-minute sweeper job that expires grants and uninstalls redeemed keys.
Backend unit tests (pure stdlib, no panel needed):

```bash
python -m unittest discover -s backend/tests -v
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
