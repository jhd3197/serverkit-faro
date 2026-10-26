import { useEffect, useState } from 'react';
import { Modal, useToast, useTranslation } from 'serverkit-sdk';
import { faroGrantsApi } from '../services/faroGrants.js';
import { EMPTY_GRANT_FORM, TTL_PRESETS, buildGrantPayload } from '../lib/grants.js';

function serverBlocker(server, t) {
    if (!server.host) return t('faro.create.noAddress', 'No address yet');
    if (!server.online) return t('faro.create.offline', 'Agent offline');
    if (!server.ssh_keys) return t('faro.create.noSshKeys', "Agent can't manage SSH keys");
    return null;
}

// Backend 400s name the servers that blocked the grant; fold them into one line.
function describeCreateError(err, t) {
    const data = err?.data || {};
    const names = data.offline || data.unsupported || data.servers || data.missing;
    const base = data.error || err?.message || t('faro.create.failed', 'Could not create the access link');
    return Array.isArray(names) && names.length ? `${base}: ${names.join(', ')}` : base;
}

/**
 * "New access" form: who it's for, which servers, how long, which login, and
 * an optional bridge (a managed jump server every connection goes through).
 */
export default function GrantCreateModal({ open, preselectServerId, onClose, onCreated }) {
    const { t } = useTranslation();
    const toast = useToast();
    const [form, setForm] = useState(EMPTY_GRANT_FORM);
    const [errors, setErrors] = useState({});
    const [servers, setServers] = useState(null);
    const [loadError, setLoadError] = useState(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!open) return;
        setErrors({});
        setForm({
            ...EMPTY_GRANT_FORM,
            serverIds: preselectServerId ? [String(preselectServerId)] : [],
        });
        let cancelled = false;
        faroGrantsApi.listServers()
            .then((data) => {
                if (cancelled) return;
                setServers(Array.isArray(data?.servers) ? data.servers : []);
                setLoadError(null);
            })
            .catch((err) => { if (!cancelled) setLoadError(err.message); });
        return () => { cancelled = true; };
    }, [open, preselectServerId]);

    const set = (patch) => setForm((prev) => ({ ...prev, ...patch }));

    const toggleServer = (id) => {
        set({
            serverIds: form.serverIds.includes(id)
                ? form.serverIds.filter((sid) => sid !== id)
                : [...form.serverIds, id],
        });
    };

    const submit = async () => {
        const { errors: found, payload } = buildGrantPayload(form);
        setErrors(found);
        if (!payload) return;
        setSaving(true);
        try {
            const result = await faroGrantsApi.createGrant(payload);
            onCreated(result.grant, result.link);
        } catch (err) {
            toast.error(describeCreateError(err, t));
        } finally {
            setSaving(false);
        }
    };

    const errorText = {
        required: t('faro.create.errRequired', 'Required'),
        tooMany: t('faro.create.errTooMany', 'A grant covers at most 64 servers'),
        invalid: t('faro.create.errUsername', 'Lowercase letters, digits, "_" or "-", up to 32 characters'),
        absolute: t('faro.create.errPath', 'Use an absolute path, like /var/www'),
        isTarget: t('faro.create.errBridgeIsTarget', 'The bridge cannot also be one of the granted servers'),
    };
    const fieldError = (field) => (errors[field]
        ? <p className="skfaro-form__error">{errorText[errors[field]]}</p>
        : null);

    const bridgeChoices = (servers || []).filter(
        (server) => !serverBlocker(server, t) && !form.serverIds.includes(String(server.id)));

    return (
        <Modal open={open} onClose={() => !saving && onClose()} title={t('faro.create.title', 'New access')} size="lg">
            <form
                data-walkthrough="faro-create-form"
                onSubmit={(event) => { event.preventDefault(); submit(); }}
            >
                <div className="skfaro-form__row">
                    <label className="form-label" htmlFor="skfaro-name">{t('faro.create.name', 'Name')}</label>
                    <input
                        id="skfaro-name"
                        className="form-input"
                        type="text"
                        autoFocus
                        maxLength={255}
                        placeholder={t('faro.create.namePlaceholder', 'Ana, contractor for Client X')}
                        value={form.name}
                        onChange={(event) => set({ name: event.target.value })}
                    />
                    <p className="skfaro-form__hint">
                        {t('faro.create.nameHint', 'Who this is for. Faro shows it on the consent screen and uses it as the connection group.')}
                    </p>
                    {fieldError('name')}
                </div>

                <div className="skfaro-form__row">
                    <span className="form-label">{t('faro.create.servers', 'Servers')}</span>
                    {loadError && <p className="skfaro-form__error">{loadError}</p>}
                    {!loadError && servers === null && (
                        <p className="skfaro-muted">{t('faro.create.loadingServers', 'Loading servers…')}</p>
                    )}
                    {servers && servers.length === 0 && (
                        <p className="skfaro-muted">{t('faro.create.noServers', 'No managed servers yet. Connect a server with the ServerKit agent first.')}</p>
                    )}
                    {servers && servers.length > 0 && (
                        <ul className="skfaro-serverlist">
                            {servers.map((server) => {
                                const id = String(server.id);
                                const blocker = serverBlocker(server, t);
                                const isBridge = form.bridgeId === id;
                                return (
                                    <li key={id} className={blocker ? 'is-disabled' : undefined}>
                                        <label>
                                            <input
                                                type="checkbox"
                                                disabled={Boolean(blocker) || isBridge}
                                                checked={form.serverIds.includes(id)}
                                                onChange={() => toggleServer(id)}
                                            />
                                            <span className="skfaro-serverlist__name">{server.name}</span>
                                            <code className="skfaro-serverlist__host">{server.host || '—'}</code>
                                            {blocker && <span className="skfaro-serverlist__why">{blocker}</span>}
                                            {isBridge && <span className="skfaro-serverlist__why">{t('faro.create.isBridge', 'Bridge')}</span>}
                                        </label>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                    {fieldError('serverIds')}
                </div>

                <div className="skfaro-form__row">
                    <span className="form-label">{t('faro.create.duration', 'Access lasts')}</span>
                    <div className="skfaro-seg" role="radiogroup">
                        {TTL_PRESETS.map((preset) => (
                            <button
                                key={preset.hours}
                                type="button"
                                role="radio"
                                aria-checked={form.ttlHours === preset.hours}
                                className={`btn btn-sm ${form.ttlHours === preset.hours ? 'btn-primary' : 'btn-ghost'}`}
                                onClick={() => set({ ttlHours: preset.hours })}
                            >
                                {t(`faro.ttl.${preset.key}`, preset.key)}
                            </button>
                        ))}
                    </div>
                    <p className="skfaro-form__hint">
                        {t('faro.create.durationHint', 'The link and the key both stop working then. On OpenSSH 8.2 or newer, sshd also refuses the key at that time even if the panel is unreachable.')}
                    </p>
                </div>

                <div className="skfaro-form__grid">
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-user">{t('faro.create.login', 'SSH login')}</label>
                        <input
                            id="skfaro-user"
                            className="form-input"
                            type="text"
                            value={form.sshUsername}
                            onChange={(event) => set({ sshUsername: event.target.value })}
                        />
                        {fieldError('sshUsername')}
                    </div>
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-path">{t('faro.create.path', 'Start folder (optional)')}</label>
                        <input
                            id="skfaro-path"
                            className="form-input"
                            type="text"
                            placeholder="/var/www"
                            value={form.path}
                            onChange={(event) => set({ path: event.target.value })}
                        />
                        {fieldError('path')}
                    </div>
                </div>
                {form.sshUsername.trim() && form.sshUsername.trim() !== 'root' && (
                    <label className="skfaro-check">
                        <input
                            type="checkbox"
                            checked={form.createUser}
                            onChange={(event) => set({ createUser: event.target.checked })}
                        />
                        {t('faro.create.createUser', 'Create this user on servers where it doesn\'t exist')}
                    </label>
                )}
                {form.sshUsername.trim() === 'root' && (
                    <p className="skfaro-form__hint">
                        {t('faro.create.rootHint', 'root gives full control of every granted server. A dedicated user is safer when the person only needs their site.')}
                    </p>
                )}

                <div className="skfaro-form__row skfaro-form__row--section">
                    <label className="form-label" htmlFor="skfaro-bridge">{t('faro.create.bridge', 'Bridge (optional)')}</label>
                    <select
                        id="skfaro-bridge"
                        className="form-select"
                        value={form.bridgeId}
                        onChange={(event) => set({ bridgeId: event.target.value })}
                    >
                        <option value="">{t('faro.create.noBridge', 'None, connect directly')}</option>
                        {bridgeChoices.map((server) => (
                            <option key={server.id} value={String(server.id)}>
                                {server.name} ({server.host})
                            </option>
                        ))}
                    </select>
                    <p className="skfaro-form__hint">
                        {t('faro.create.bridgeHint', 'Faro connects through this server first. It gets the same key, limited to reaching the granted servers on port 22, so every person arrives from one static address.')}
                    </p>
                    {fieldError('bridgeId')}
                </div>
                {form.bridgeId && (
                    <div className="skfaro-form__grid">
                        <div className="skfaro-form__row">
                            <label className="form-label" htmlFor="skfaro-bridge-user">{t('faro.create.bridgeLogin', 'Bridge login')}</label>
                            <input
                                id="skfaro-bridge-user"
                                className="form-input"
                                type="text"
                                placeholder={form.sshUsername || 'root'}
                                value={form.bridgeUsername}
                                onChange={(event) => set({ bridgeUsername: event.target.value })}
                            />
                            {fieldError('bridgeUsername')}
                        </div>
                        <label className="skfaro-check skfaro-check--inline">
                            <input
                                type="checkbox"
                                checked={form.restrictSource}
                                onChange={(event) => set({ restrictSource: event.target.checked })}
                            />
                            <span>
                                {t('faro.create.restrictSource', 'Only accept the key from the bridge')}
                                <span className="skfaro-form__hint skfaro-form__hint--block">
                                    {t('faro.create.restrictSourceHint', "Adds from=\"bridge address\" on the granted servers. Leave off if the bridge reaches them over a private network address the panel doesn't know.")}
                                </span>
                            </span>
                        </label>
                    </div>
                )}

                <div className="skfaro-actions">
                    <button type="submit" className="btn btn-primary" disabled={saving} data-walkthrough="faro-create-submit">
                        {saving ? t('faro.create.creating', 'Creating…') : t('faro.create.submit', 'Create link')}
                    </button>
                    <button type="button" className="btn btn-ghost" disabled={saving} onClick={onClose}>
                        {t('faro.create.cancel', 'Cancel')}
                    </button>
                </div>
            </form>
        </Modal>
    );
}
