// Pure builders for Faro `faro://` deep links.
//
// Contract mirrors Faro's own deeplink parser (Faro/docs/deep-links.md +
// src-tauri/src/deeplink.rs). Kept dependency-free and side-effect-free so it
// can be unit-tested with `node --test` without a browser or React.
//
// SECURITY: a faro:// link only ever *prefills* Faro's connection editor — it
// never connects and NEVER carries a password/secret/passphrase. Faro drops
// those params defensively; we never emit them in the first place.

const SECRET_PARAMS = new Set(['password', 'secret', 'passphrase', 'token', 'key']);

function encodeParams(params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (SECRET_PARAMS.has(k)) continue;          // never emit credentials
        if (v === null || v === undefined) continue; // drop empty/unknown fields
        const s = String(v).trim();
        if (s === '') continue;
        q.append(k, s);
    }
    return q.toString();
}

/**
 * Build a `faro://connect` link that prefills the SFTP/FTP/S3 editor.
 * Only `host` is required; `port`/`username` are optional — Faro defaults the
 * port per protocol and the user types the username on the prefilled form.
 *
 * @param {object} p
 * @param {string} p.host                      server hostname or IP (required)
 * @param {string} [p.protocol='sftp']         sftp|ftp|ftps|s3|azure
 * @param {string|number} [p.port]
 * @param {string} [p.username]
 * @param {string} [p.path]                     default remote directory to open
 * @param {string} [p.name]                     friendly connection name
 * @returns {string|null} the link, or null if no usable host
 */
export function buildFaroConnect({ host, protocol = 'sftp', port, username, path, name } = {}) {
    if (!host || String(host).trim() === '') return null;
    const query = encodeParams({ protocol, host, port, username, path, name });
    return `faro://connect?${query}`;
}
