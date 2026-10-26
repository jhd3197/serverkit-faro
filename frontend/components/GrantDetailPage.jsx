import { useCallback, useEffect, useState } from 'react';
import {
    DataTable,
    Link,
    Modal,
    Pill,
    formatRelativeTime,
    useConfirm,
    useNavigate,
    useParams,
    useToast,
    useTranslation,
} from 'serverkit-sdk';
import { faroGrantsApi } from '../services/faroGrants.js';
import { TTL_PRESETS, grantKind, isOpen, keyKind } from '../lib/grants.js';
import { useGrantLabels } from './grantLabels.js';

function DetailItem({ label, children }) {
    return (
        <div className="skfaro-detail__item">
            <dt>{label}</dt>
            <dd>{children}</dd>
        </div>
    );
}

function when(iso) {
    return iso ? formatRelativeTime(iso) : '—';
}

/**
 * /faro/grants/:id — one grant: what it gives, where the key actually is
 * (per server, including the bridge), and the actions to fix or end it.
 */
export default function GrantDetailPage() {
    const { id } = useParams();
    const { t } = useTranslation();
    const toast = useToast();
    const { confirm } = useConfirm();
    const navigate = useNavigate();
    const labels = useGrantLabels();

    const [grant, setGrant] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(null);
    const [extendOpen, setExtendOpen] = useState(false);
    const [extendHours, setExtendHours] = useState(72);

    const load = useCallback(async (background = false) => {
        if (!background) setLoading(true);
        try {
            const data = await faroGrantsApi.getGrant(id);
            setGrant(data?.grant || null);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    const waiting = grant && (grant.status === 'active' || grant.status === 'redeeming');
    useEffect(() => {
        if (!waiting) return undefined;
        const timer = setInterval(() => load(true), 10000);
        return () => clearInterval(timer);
    }, [waiting, load]);

    // Every action returns { grant, failed }; one place to apply it.
    const run = async (name, fn, { success, partial }) => {
        setBusy(name);
        try {
            const result = await fn();
            if (result?.grant) setGrant(result.grant);
            const failed = result?.failed || [];
            if (failed.length) toast.warning(partial(failed.length));
            else toast.success(success);
            return true;
        } catch (err) {
            toast.error(err?.data?.error || err.message);
            return false;
        } finally {
            setBusy(null);
        }
    };

    const handleRevoke = async () => {
        const ok = await confirm({
            title: t('faro.revoke.title', 'Revoke "{{name}}"?', { name: grant.name }),
            message: t('faro.revoke.message', 'The link stops working and the key is removed from every server it was installed on. Faro connections using it start failing right away.'),
            confirmText: t('faro.revoke.confirm', 'Revoke access'),
            variant: 'danger',
        });
        if (!ok) return;
        await run('revoke', () => faroGrantsApi.revokeGrant(grant.id), {
            success: t('faro.revoke.success', 'Access revoked'),
            partial: (count) => t('faro.revoke.partial', 'Revoked, but the key could not be removed from {{count}} server(s) yet. It will keep retrying.', { count }),
        });
    };

    const handleRetry = () => run('retry', () => faroGrantsApi.retryGrant(grant.id), {
        success: t('faro.retry.success', 'Done. Every server is up to date.'),
        partial: (count) => t('faro.retry.partial', '{{count}} server(s) still failing. See the errors below.', { count }),
    });

    const handleExtend = async () => {
        const ok = await run('extend', () => faroGrantsApi.extendGrant(grant.id, extendHours), {
            success: t('faro.extend.success', 'Access extended'),
            partial: (count) => t('faro.extend.partial', 'Extended, but {{count}} server(s) kept the old key expiry. Retry from this page.', { count }),
        });
        if (ok) setExtendOpen(false);
    };

    const handleDelete = async () => {
        const ok = await confirm({
            title: t('faro.delete.title', 'Delete "{{name}}"?', { name: grant.name }),
            message: t('faro.delete.message', 'Removes this record from the list. The key is already gone from every server. The audit log keeps its history.'),
            confirmText: t('faro.delete.confirm', 'Delete record'),
            variant: 'danger',
        });
        if (!ok) return;
        try {
            await faroGrantsApi.deleteGrant(grant.id);
            toast.success(t('faro.delete.success', 'Record deleted'));
            navigate('/faro');
        } catch (err) {
            toast.error(err?.data?.error || err.message);
        }
    };

    const back = (
        <Link to="/faro" className="skfaro-back">
            &larr; {t('faro.detail.back', 'All access')}
        </Link>
    );

    if (loading && !grant) {
        return <div className="skfaro-page">{back}<p className="skfaro-muted">{t('faro.page.loading', 'Loading access…')}</p></div>;
    }
    if (error || !grant) {
        return (
            <div className="skfaro-page">
                {back}
                <p className="skfaro-form__error">{error || t('faro.detail.notFound', 'This access grant does not exist anymore.')}</p>
            </div>
        );
    }

    const targets = grant.targets || [];
    const finished = grant.status === 'revoked' || grant.status === 'expired';
    const needsRetry = grant.health === 'partial' || grant.health === 'cleanup';

    const memberGrant = Boolean(grant.member_id);
    const columns = [
        ...(memberGrant ? [{
            key: 'device_label',
            header: t('faro.detail.device', 'Device'),
            sortable: true,
            hideable: false,
            render: (row) => row.device_label || '—',
        }] : []),
        {
            key: 'name',
            header: t('faro.detail.server', 'Server'),
            sortable: true,
            hideable: false,
            render: (row) => <strong>{row.name || row.server_id}</strong>,
        },
        {
            key: 'role',
            header: t('faro.detail.role', 'Role'),
            sortable: true,
            render: (row) => (row.role === 'jump'
                ? t('faro.detail.roleBridge', 'Bridge')
                : t('faro.detail.roleTarget', 'Granted')),
        },
        {
            key: 'host',
            header: t('faro.detail.host', 'Address'),
            render: (row) => <code>{row.host || '—'}</code>,
        },
        {
            key: 'key_status',
            header: t('faro.detail.key', 'Key'),
            sortable: true,
            render: (row) => <Pill kind={keyKind(row.key_status)}>{labels.key(row.key_status)}</Pill>,
        },
        {
            key: 'expiry_enforced',
            header: t('faro.detail.sshdExpiry', 'sshd expiry'),
            render: (row) => (row.key_status === 'installed'
                ? (row.expiry_enforced ? t('faro.detail.yes', 'Yes') : t('faro.detail.noOldSsh', 'No (OpenSSH < 8.2)'))
                : '—'),
        },
        {
            key: 'error',
            header: t('faro.detail.lastError', 'Last error'),
            render: (row) => (row.error && ['failed', 'remove_failed', 'pending'].includes(row.key_status)
                ? <span className="skfaro-form__error">{row.error}</span>
                : '—'),
        },
        {
            key: 'updated_at',
            header: t('faro.detail.updated', 'Updated'),
            sortable: true,
            render: (row) => when(row.updated_at),
        },
    ];

    return (
        <div className="skfaro-page">
            {back}

            <div className="skfaro-page__header">
                <div className="skfaro-detail__heading">
                    <h2 className="skfaro-page__title">{grant.name}</h2>
                    <Pill kind={grantKind(grant)}>{labels.grant(grant)}</Pill>
                </div>
                <div className="skfaro-actions skfaro-actions--tight">
                    {needsRetry && (
                        <button type="button" className="btn btn-primary btn-sm" disabled={busy !== null} onClick={handleRetry}>
                            {grant.health === 'cleanup'
                                ? t('faro.detail.retryRemoval', 'Retry key removal')
                                : t('faro.detail.retryInstall', 'Retry failed servers')}
                        </button>
                    )}
                    {isOpen(grant) && (
                        <button type="button" className="btn btn-ghost btn-sm" disabled={busy !== null} onClick={() => setExtendOpen(true)}>
                            {t('faro.detail.extend', 'Extend')}
                        </button>
                    )}
                    {isOpen(grant) && (
                        <button type="button" className="btn btn-danger btn-sm" disabled={busy !== null} onClick={handleRevoke}>
                            {t('faro.table.revoke', 'Revoke')}
                        </button>
                    )}
                    {finished && grant.health !== 'cleanup' && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={handleDelete}>
                            {t('faro.detail.delete', 'Delete record')}
                        </button>
                    )}
                </div>
            </div>

            {grant.health === 'partial' && (
                <div className="skfaro-notice skfaro-notice--warn">
                    <strong>{t('faro.detail.partialTitle', 'Some servers did not get the key')}</strong>
                    <p>{t('faro.detail.partialBody', 'The person can reach the servers marked Installed. Fix the error shown for the others (often an offline agent), then retry.')}</p>
                </div>
            )}
            {grant.health === 'cleanup' && (
                <div className="skfaro-notice skfaro-notice--warn">
                    <strong>{t('faro.detail.cleanupTitle', 'The key is still on some servers')}</strong>
                    <p>{t('faro.detail.cleanupBody', 'Access is revoked in the panel, but these servers could not be reached to remove the key. ServerKit retries every 5 minutes for a day; you can retry now.')}</p>
                </div>
            )}
            {grant.status === 'active' && (
                <div className="skfaro-notice">
                    <strong>{t('faro.detail.waitingTitle', 'Waiting for the person to accept')}</strong>
                    <p>{t('faro.detail.waitingBody', 'The link was shown once when you created it. If it was lost, revoke this and create a new one.')}</p>
                </div>
            )}

            {memberGrant && targets.length === 0 && isOpen(grant) && (
                <div className="skfaro-notice">
                    <strong>{t('faro.detail.noDevicesTitle', 'No devices yet')}</strong>
                    <p>{t('faro.detail.noDevicesBody', 'This access applies to every approved device of the member. Add a device from the member page to use it.')}</p>
                </div>
            )}

            <div className="card skfaro-detail">
                <dl className="skfaro-detail__grid">
                    {memberGrant && (
                        <DetailItem label={t('faro.detail.member', 'Member')}>
                            <Link to={`/faro/members/${grant.member_id}`}>{grant.member_name}</Link>
                        </DetailItem>
                    )}
                    <DetailItem label={t('faro.detail.login', 'SSH login')}>
                        <code>{grant.ssh_username}</code>
                        {grant.create_user ? ` · ${t('faro.detail.createdIfMissing', 'created if missing')}` : ''}
                    </DetailItem>
                    <DetailItem label={t('faro.detail.path', 'Start folder')}>
                        {grant.default_path ? <code>{grant.default_path}</code> : '—'}
                    </DetailItem>
                    <DetailItem label={t('faro.detail.bridge', 'Bridge')}>
                        {grant.jump
                            ? <>{grant.jump.name || grant.jump.host} <code>{grant.jump.username || grant.ssh_username}@{grant.jump.host}</code>{grant.restrict_source ? ` · ${t('faro.detail.sourceLocked', 'key only accepted from the bridge')}` : ''}</>
                            : t('faro.detail.direct', 'None, direct connection')}
                    </DetailItem>
                    <DetailItem label={isOpen(grant) ? t('faro.detail.ends', 'Ends') : t('faro.detail.ended', 'Ended')}>
                        {isOpen(grant)
                            ? `${when(grant.expires_at)} · ${new Date(grant.expires_at).toLocaleString()}`
                            : when(grant.revoked_at || grant.expires_at)}
                    </DetailItem>
                    <DetailItem label={t('faro.detail.created', 'Created')}>{when(grant.created_at)}</DetailItem>
                    {!memberGrant && <DetailItem label={t('faro.detail.accepted', 'Accepted')}>
                        {grant.redeemed_at
                            ? `${when(grant.redeemed_at)}${grant.redeemed_ip ? ` · ${grant.redeemed_ip}` : ''}`
                            : '—'}
                    </DetailItem>}
                    {!memberGrant && (
                        <DetailItem label={t('faro.detail.fingerprint', 'Key fingerprint')}>
                            {grant.key_fingerprint ? <code>{grant.key_fingerprint}</code> : '—'}
                        </DetailItem>
                    )}
                </dl>
            </div>

            <h3 className="skfaro-section-title">{t('faro.detail.serversTitle', 'Where the key is')}</h3>
            <DataTable
                columns={columns}
                data={targets}
                keyField="id"
                storageKey="serverkit-table-faro-grant-targets"
            />

            <Modal
                open={extendOpen}
                onClose={() => busy === null && setExtendOpen(false)}
                title={t('faro.extend.title', 'Extend "{{name}}"', { name: grant.name })}
            >
                <p>{t('faro.extend.intro', 'Access will end this long from now:')}</p>
                <div className="skfaro-seg" role="radiogroup">
                    {TTL_PRESETS.map((preset) => (
                        <button
                            key={preset.hours}
                            type="button"
                            role="radio"
                            aria-checked={extendHours === preset.hours}
                            className={`btn btn-sm ${extendHours === preset.hours ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setExtendHours(preset.hours)}
                        >
                            {t(`faro.ttl.${preset.key}`, preset.key)}
                        </button>
                    ))}
                </div>
                <div className="skfaro-actions">
                    <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={handleExtend}>
                        {busy === 'extend' ? t('faro.extend.saving', 'Extending…') : t('faro.extend.submit', 'Extend access')}
                    </button>
                    <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => setExtendOpen(false)}>
                        {t('faro.create.cancel', 'Cancel')}
                    </button>
                </div>
            </Modal>
        </div>
    );
}
