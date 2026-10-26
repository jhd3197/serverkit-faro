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

    // ---- members, devices, invites ----
    listMembers: () => api.request(`${BASE_PATH}/members`),

    // -> { member, devices, grants, invites, keys }
    getMember: (id) => api.request(`${BASE_PATH}/members/${encodeURIComponent(id)}`),

    createMember: (body) => api.request(`${BASE_PATH}/members`, { method: 'POST', body }),

    updateMember: (id, body) => api.request(
        `${BASE_PATH}/members/${encodeURIComponent(id)}`, { method: 'PATCH', body }),

    deleteMember: (id) => api.request(
        `${BASE_PATH}/members/${encodeURIComponent(id)}`, { method: 'DELETE' }),

    revokeMember: (id) => api.request(
        `${BASE_PATH}/members/${encodeURIComponent(id)}/revoke-all`, { method: 'POST' }),

    syncMember: (id) => api.request(
        `${BASE_PATH}/members/${encodeURIComponent(id)}/sync`, { method: 'POST' }),

    // -> { invite, link }; the link is only ever returned here, once.
    createInvite: (memberId, body) => api.request(
        `${BASE_PATH}/members/${encodeURIComponent(memberId)}/invites`, { method: 'POST', body }),

    revokeInvite: (id) => api.request(
        `${BASE_PATH}/invites/${encodeURIComponent(id)}/revoke`, { method: 'POST' }),

    deviceAction: (id, action) => api.request(
        `${BASE_PATH}/devices/${encodeURIComponent(id)}/${action}`, { method: 'POST' }),

    renameDevice: (id, label) => api.request(
        `${BASE_PATH}/devices/${encodeURIComponent(id)}`, { method: 'PATCH', body: { label } }),
};
