// Pure helpers for the access-grant UI: status tones, TTL presets and the
// create-form → API payload mapping. No React, no SDK — unit-tested with
// `node --test`.

// Panel Pill kinds: green | amber | red | cyan | violet | gray.
const STATUS_KIND = {
    active: 'cyan',      // link issued, waiting for the person to accept
    redeeming: 'cyan',
    redeemed: 'green',   // key installed, access live
    revoked: 'gray',
    expired: 'gray',
};

const KEY_KIND = {
    installed: 'green',
    pending: 'gray',
    removed: 'gray',
    failed: 'red',
    remove_failed: 'red',
};

/** Pill kind for a grant: health problems win over the plain status. */
export function grantKind(grant) {
    if (grant?.health === 'partial' || grant?.health === 'cleanup') return 'amber';
    return STATUS_KIND[grant?.status] || 'gray';
}

export function keyKind(status) {
    return KEY_KIND[status] || 'gray';
}

/** Is access live right now (key installed, not expired)? */
export function isLive(grant) {
    return grant?.status === 'redeemed';
}

/** Can the admin still act on the grant's lifetime (extend / revoke)? */
export function isOpen(grant) {
    return grant?.status === 'active' || grant?.status === 'redeemed';
}

export const TTL_PRESETS = [
    { hours: 4, key: '4h' },
    { hours: 24, key: '24h' },
    { hours: 72, key: '3d' },
    { hours: 168, key: '7d' },
    { hours: 720, key: '30d' },
];

export const DEFAULT_TTL_HOURS = 72;
export const MAX_SERVERS = 64;

const USERNAME_RE = /^[a-z_][a-z0-9_-]{0,31}$/;

export const EMPTY_GRANT_FORM = {
    name: '',
    serverIds: [],
    ttlHours: DEFAULT_TTL_HOURS,
    sshUsername: 'root',
    createUser: false,
    path: '',
    bridgeId: '',
    bridgeUsername: '',
    restrictSource: false,
};

/**
 * Validate the create form. Returns { errors: {field: code}, payload|null }.
 * Error codes are stable strings the component maps to translated text.
 */
export function buildGrantPayload(form) {
    const errors = {};
    const name = (form.name || '').trim();
    if (!name) errors.name = 'required';

    const serverIds = [...new Set(form.serverIds || [])];
    if (serverIds.length === 0) errors.serverIds = 'required';
    else if (serverIds.length > MAX_SERVERS) errors.serverIds = 'tooMany';

    const sshUsername = (form.sshUsername || '').trim() || 'root';
    if (!USERNAME_RE.test(sshUsername)) errors.sshUsername = 'invalid';

    const path = (form.path || '').trim();
    if (path && !path.startsWith('/')) errors.path = 'absolute';

    const bridgeId = form.bridgeId || '';
    if (bridgeId && serverIds.includes(bridgeId)) errors.bridgeId = 'isTarget';
    const bridgeUsername = (form.bridgeUsername || '').trim();
    if (bridgeId && bridgeUsername && !USERNAME_RE.test(bridgeUsername)) {
        errors.bridgeUsername = 'invalid';
    }

    if (Object.keys(errors).length) return { errors, payload: null };

    const payload = {
        name,
        server_ids: serverIds,
        ttl_hours: Number(form.ttlHours) || DEFAULT_TTL_HOURS,
        ssh_username: sshUsername,
    };
    if (path) payload.path = path;
    if (sshUsername !== 'root' && form.createUser) payload.create_user = true;
    if (bridgeId) {
        payload.jump = { server_id: bridgeId };
        if (bridgeUsername) payload.jump.username = bridgeUsername;
        if (form.restrictSource) payload.restrict_source = true;
    }
    return { errors, payload };
}

/** Counts for the list header. */
export function summarize(grants) {
    const out = { live: 0, waiting: 0, attention: 0 };
    for (const grant of grants || []) {
        if (grant.status === 'redeemed') out.live += 1;
        if (grant.status === 'active') out.waiting += 1;
        if (grant.health === 'partial' || grant.health === 'cleanup') out.attention += 1;
    }
    return out;
}
