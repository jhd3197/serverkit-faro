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

## Requirements

- **ServerKit panel** ≥ 1.7.0 (runtime-ESM extension loader). Full WordPress
  support needs a panel build that ships the `wordpress.detail.panel` slot.
- **Faro** desktop app installed on the machine viewing the panel, with the
  `faro://` scheme registered (Faro's installer does this; dev builds register
  it at startup).

## Pure-frontend

No backend, no database, no server permissions — the extension only generates
links in the browser from data the panel already exposes (`/apps/:id`,
`/servers/:id`, `/wordpress/sites/:id`).

## Build & release

```bash
cd frontend
npm install
npm run build          # → frontend/dist/index.mjs (CSS inlined)
npm test               # unit-tests the faro:// link builder

# from the repo root — stage + zip the release artifact
./scripts/build-zip.sh # → dist/serverkit-faro-<version>.zip
sha256sum dist/serverkit-faro-*.zip
```

Attach the zip to a GitHub release, record its sha256, and add/update the
entry in `serverkit-extensions/index.json` (`bundled: false`, with `source`
pointing at the release asset and the recorded `sha256`).

## License

MIT © Juan Denis
