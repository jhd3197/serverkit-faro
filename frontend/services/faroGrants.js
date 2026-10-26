// The panel's ApiClient via the SDK singleton (JWT refresh, workspace headers,
// error normalization included) — the extension's own client for the
// access-grant routes under the manifest's url_prefix.
import { api } from 'serverkit-sdk';

const BASE_PATH = '/faro-grants';

export const faroGrantsApi = {
    listServers: () => api.request(`${BASE_PATH}/servers`),

    listGrants: () => api.request(`${BASE_PATH}/grants`),

    getGrant: (id) => api.request(`${BASE_PATH}/grants/${encodeURIComponent(id)}`),

    // -> { grant, link }. The link carries the raw token and is only ever
    // returned here, once.
    createGrant: (body) => api.request(`${BASE_PATH}/grants`, { method: 'POST', body }),

    revokeGrant: (id) => api.request(
        `${BASE_PATH}/grants/${encodeURIComponent(id)}/revoke`, { method: 'POST' }),

    extendGrant: (id, ttlHours) => api.request(
        `${BASE_PATH}/grants/${encodeURIComponent(id)}/extend`,
        { method: 'POST', body: { ttl_hours: ttlHours } }),

    retryGrant: (id) => api.request(
        `${BASE_PATH}/grants/${encodeURIComponent(id)}/retry`, { method: 'POST' }),

    deleteGrant: (id) => api.request(
        `${BASE_PATH}/grants/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
