// Resolves a panel entity into a Faro connection target by walking
// application → server and reading the server's reachable address.
//
// SFTP connects to the *server box*, so the host is the server's ip_address
// (falling back to hostname), NOT the public domain. All calls go through the
// panel's own ApiService (externalized as `serverkit-sdk`) using the same
// `api.request(path)` convention as other extensions.

import { api } from 'serverkit-sdk';

async function req(path) {
    if (!api || typeof api.request !== 'function') {
        throw new Error('serverkit-sdk api.request is unavailable');
    }
    return api.request(path);
}

// Endpoints may return the resource bare or wrapped ({ app }, { server }, …).
function unwrap(res, ...keys) {
    if (!res) return null;
    for (const k of keys) {
        if (res[k]) return res[k];
    }
    return res;
}

/** Reachable address for a server id: ip_address, else hostname, else null. */
export async function serverHost(serverId) {
    if (!serverId) return null;
    const server = unwrap(await req(`/servers/${serverId}`), 'server');
    return server?.ip_address || server?.hostname || null;
}

/** Target from an already-fetched application dict (has server_id, root_path). */
export async function targetFromApp(app, { name } = {}) {
    if (!app) return null;
    const host = await serverHost(app.server_id);
    if (!host) return null;
    return {
        host,
        path: app.root_path || undefined,
        name: name || app.name || undefined,
    };
}

/** Target from an application id: fetch the app, then resolve its server. */
export async function targetFromAppId(appId, opts) {
    if (!appId) return null;
    const app = unwrap(await req(`/apps/${appId}`), 'app', 'application');
    return targetFromApp(app, opts);
}
