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
import { TTL_PRESETS, deviceKind, grantKind, isOpen, keyKind } from '../lib/grants.js';
import GrantCreateModal from './GrantCreateModal.jsx';
import GrantLinkModal from './GrantLinkModal.jsx';
import { useGrantLabels } from './grantLabels.js';

function when(iso) {
    return iso ? formatRelativeTime(iso) : '—';
}

/**
 * /faro/members/:id — one person: devices waiting for approval, their
 * devices, the access they have, open add-device links, and where each
 * device's key actually is.
 */
export default function MemberDetailPage() {
    const { id } = useParams();
    const { t } = useTranslation();
    const toast = useToast();
    const { confirm } = useConfirm();
    const navigate = useNavigate();
    const labels = useGrantLabels();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(null);

    const [grantOpen, setGrantOpen] = useState(false);
    const [inviteOpen, setInviteOpen] = useState(false);
    const [inviteForm, setInviteForm] = useState({ ttlHours: 72, requireApproval: true });
    const [issued, setIssued] = useState(null); // { invite, link }
    const [renaming, setRenaming] = useState(null); // { id, label }

    const load = useCallback(async (background = false) => {
        if (!background) setLoading(true);
        try {
            setData(await faroGrantsApi.getMember(id));
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    // Poll while an add-device link is out, so a new device shows up live.
    const openInvites = (data?.invites || []).some((invite) => invite.status === 'active');
    useEffect(() => {
        if (!openInvites) return undefined;
        const timer = setInterval(() => load(true), 10000);
        return () => clearInterval(timer);
    }, [openInvites, load]);

    const act = async (key, fn, success, partial) => {
        setBusy(key);
        try {
            const result = await fn();
            const failed = result?.failed || [];
            if (failed.length && partial) toast.warning(partial(failed.length));
            else if (success) toast.success(success);
            await load(true);
            return result;
        } catch (err) {
            toast.error(err?.data?.error || err.message);
            return null;
        } finally {
            setBusy(null);
        }
    };

    const partialKeys = (count) => t('faro.member.partial', 'Saved, but {{count}} key change(s) failed. See "Where the keys are" below.', { count });

    const deviceAction = async (device, action) => {
        if (action === 'revoke' || action === 'reject') {
            const ok = await confirm({
                title: action === 'revoke'
                    ? t('faro.device.revokeTitle', 'Revoke "{{label}}"?', { label: device.label })
                    : t('faro.device.rejectTitle', 'Reject "{{label}}"?', { label: device.label }),
                message: action === 'revoke'
                    ? t('faro.device.revokeBody', "This device's key is removed from every server. The member's other devices keep working.")
                    : t('faro.device.rejectBody', 'The device never gets access. Send a new add-device link if this was a mistake.'),
                confirmText: action === 'revoke' ? t('faro.device.revoke', 'Revoke device') : t('faro.device.reject', 'Reject'),
                variant: 'danger',
            });
            if (!ok) return;
        }
        const success = {
            approve: t('faro.device.approved', 'Device approved'),
            reject: t('faro.device.rejected', 'Device rejected'),
            revoke: t('faro.device.revoked', 'Device revoked'),
        }[action];
        await act(`device-${device.id}`, () => faroGrantsApi.deviceAction(device.id, action), success, partialKeys);
    };

    const createInvite = async () => {
        const result = await act('invite', () => faroGrantsApi.createInvite(id, {
            ttl_hours: inviteForm.ttlHours, require_approval: inviteForm.requireApproval,
        }));
        if (result?.link) {
            setInviteOpen(false);
            setIssued(result);
        }
    };

    const revokeAll = async () => {
        const ok = await confirm({
            title: t('faro.member.revokeAllTitle', 'Revoke all access for {{name}}?', { name: data.member.name }),
            message: t('faro.member.revokeAllBody', 'Every grant, device and open add-device link is revoked and every key is removed. Use this when someone leaves.'),
            confirmText: t('faro.member.revokeAll', 'Revoke everything'),
            variant: 'danger',
        });
        if (!ok) return;
        await act('revoke-all', () => faroGrantsApi.revokeMember(id),
            t('faro.member.revokedAll', 'All access revoked'), partialKeys);
    };

    const remove = async () => {
        const ok = await confirm({
            title: t('faro.member.deleteTitle', 'Delete {{name}}?', { name: data.member.name }),
            message: t('faro.member.deleteBody', 'Removes the member, their devices and access history from this list. The audit log keeps the record.'),
            confirmText: t('faro.member.delete', 'Delete member'),
            variant: 'danger',
        });
        if (!ok) return;
        try {
            await faroGrantsApi.deleteMember(id);
            toast.success(t('faro.member.deleted', 'Member deleted'));
            navigate('/faro/members');
        } catch (err) {
            toast.error(err?.data?.error || err.message);
        }
    };

    const saveRename = async () => {
        const label = (renaming?.label || '').trim();
        if (!label) return;
        const result = await act('rename', () => faroGrantsApi.renameDevice(renaming.id, label),
            t('faro.device.renamed', 'Device renamed'));
        if (result) setRenaming(null);
    };

    const back = <Link to="/faro/members" className="skfaro-back">&larr; {t('faro.member.back', 'All members')}</Link>;

    if (loading && !data) {
        return <div className="skfaro-page">{back}<p className="skfaro-muted">{t('faro.members.loading', 'Loading members…')}</p></div>;
    }
    if (error || !data) {
        return <div className="skfaro-page">{back}<p className="skfaro-form__error">{error || t('faro.member.notFound', 'This member does not exist anymore.')}</p></div>;
    }

    const { member, devices, grants, invites, keys } = data;
    const pending = devices.filter((d) => d.status === 'pending');
    const shownDevices = devices.filter((d) => d.status !== 'pending');
    const activeInvites = invites.filter((i) => i.status === 'active');
    const liveGrants = grants.filter(isOpen);
    const failing = keys.filter((k) => ['failed', 'remove_failed'].includes(k.key_status));
    const visibleKeys = keys.filter((k) => k.key_status !== 'removed');
    const hasLiveAccess = liveGrants.length > 0 || devices.some((d) => ['active', 'pending'].includes(d.status));

    const deviceColumns = [
        { key: 'label', header: t('faro.device.label', 'Device'), sortable: true, hideable: false,
            render: (d) => <strong>{d.label}</strong> },
        { key: 'status', header: t('faro.device.status', 'Status'), sortable: true,
            render: (d) => <Pill kind={deviceKind(d.status)}>{labels.device(d.status)}</Pill> },
        { key: 'fingerprint', header: t('faro.device.fingerprint', 'Key fingerprint'),
            render: (d) => <code className="skfaro-fp">{d.fingerprint || '—'}</code> },
        { key: 'redeemed_ip', header: t('faro.device.ip', 'Added from'), render: (d) => d.redeemed_ip || '—' },
        { key: 'created_at', header: t('faro.device.added', 'Added'), sortable: true, render: (d) => when(d.created_at) },
        { key: 'actions', header: '', sortable: false, hideable: false, className: 'text-right',
            render: (d) => (
                <div className="skfaro-table__actions">
                    {d.status === 'active' && (
                        <>
                            <button type="button" className="btn btn-ghost btn-sm"
                                onClick={() => setRenaming({ id: d.id, label: d.label })}>
                                {t('faro.device.rename', 'Rename')}
                            </button>
                            <button type="button" className="btn btn-danger btn-sm" disabled={busy !== null}
                                onClick={() => deviceAction(d, 'revoke')}>
                                {t('faro.table.revoke', 'Revoke')}
                            </button>
                        </>
                    )}
                </div>
            ) },
    ];

    const grantColumns = [
        { key: 'name', header: t('faro.table.name', 'Name'), sortable: true, hideable: false,
            render: (g) => <strong>{g.name}</strong> },
        { key: 'servers', header: t('faro.table.servers', 'Servers'),
            render: (g) => (g.servers || []).map((s) => s.name).join(', ') || '—' },
        { key: 'ssh_username', header: t('faro.table.login', 'Login'), render: (g) => <code>{g.ssh_username}</code> },
        { key: 'bridge', header: t('faro.table.bridge', 'Bridge'), render: (g) => (g.jump ? (g.jump.name || g.jump.host) : '—') },
        { key: 'status', header: t('faro.table.status', 'Status'), sortable: true,
            render: (g) => <Pill kind={grantKind(g)}>{labels.grant(g)}</Pill> },
        { key: 'expires_at', header: t('faro.table.expires', 'Ends'), sortable: true,
            render: (g) => (isOpen(g) ? when(g.expires_at) : '—') },
    ];

    const keyColumns = [
        { key: 'device_label', header: t('faro.detail.device', 'Device'), sortable: true, hideable: false,
            render: (k) => k.device_label || '—' },
        { key: 'name', header: t('faro.detail.server', 'Server'), sortable: true,
            render: (k) => <strong>{k.name || k.server_id}</strong> },
        { key: 'role', header: t('faro.detail.role', 'Role'),
            render: (k) => (k.role === 'jump' ? t('faro.detail.roleBridge', 'Bridge') : t('faro.detail.roleTarget', 'Granted')) },
        { key: 'username', header: t('faro.table.login', 'Login'), render: (k) => <code>{k.username}</code> },
        { key: 'key_status', header: t('faro.detail.key', 'Key'), sortable: true,
            render: (k) => <Pill kind={keyKind(k.key_status)}>{labels.key(k.key_status)}</Pill> },
        { key: 'expires_at', header: t('faro.member.keyExpiry', 'Key ends'),
            render: (k) => (k.key_status === 'installed' ? when(k.expires_at) : '—') },
        { key: 'error', header: t('faro.detail.lastError', 'Last error'),
            render: (k) => (k.error ? <span className="skfaro-form__error">{k.error}</span> : '—') },
    ];

    return (
        <div className="skfaro-page">
            {back}
            <div className="skfaro-page__header">
                <div>
                    <h2 className="skfaro-page__title">{member.name}</h2>
                    {(member.email || member.note) && (
                        <p className="skfaro-muted">{[member.email, member.note].filter(Boolean).join(' · ')}</p>
                    )}
                </div>
                <div className="skfaro-actions skfaro-actions--tight">
                    {failing.length > 0 && (
                        <button type="button" className="btn btn-primary btn-sm" disabled={busy !== null}
                            onClick={() => act('sync', () => faroGrantsApi.syncMember(id),
                                t('faro.retry.success', 'Done. Every server is up to date.'), partialKeys)}>
                            {t('faro.member.retry', 'Retry failed keys')}
                        </button>
                    )}
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => setGrantOpen(true)}>
                        {t('faro.member.giveAccess', 'Give access')}
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" disabled={liveGrants.length === 0}
                        title={liveGrants.length === 0 ? t('faro.member.addDeviceNeedsAccess', 'Give access to at least one server first') : undefined}
                        onClick={() => setInviteOpen(true)}>
                        {t('faro.member.addDevice', 'Add device')}
                    </button>
                    {hasLiveAccess && (
                        <button type="button" className="btn btn-danger btn-sm" disabled={busy !== null} onClick={revokeAll}>
                            {t('faro.member.revokeAll', 'Revoke everything')}
                        </button>
                    )}
                    {!hasLiveAccess && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={remove}>
                            {t('faro.member.delete', 'Delete member')}
                        </button>
                    )}
                </div>
            </div>

            {pending.length > 0 && (
                <div className="skfaro-notice skfaro-notice--warn">
                    <strong>{t('faro.member.pendingTitle', 'Waiting for your approval')}</strong>
                    <p>{t('faro.member.pendingBody', 'Approve only devices you expect. Check the fingerprint with the person if in doubt: Faro shows it on their side.')}</p>
                    <ul className="skfaro-pending">
                        {pending.map((d) => (
                            <li key={d.id}>
                                <div>
                                    <strong>{d.label}</strong>
                                    <span className="skfaro-muted"> · {when(d.created_at)}{d.redeemed_ip ? ` · ${d.redeemed_ip}` : ''}</span>
                                    <div><code className="skfaro-fp">{d.fingerprint}</code></div>
                                </div>
                                <div className="skfaro-table__actions">
                                    <button type="button" className="btn btn-primary btn-sm" disabled={busy !== null}
                                        onClick={() => deviceAction(d, 'approve')}>
                                        {t('faro.device.approve', 'Approve')}
                                    </button>
                                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy !== null}
                                        onClick={() => deviceAction(d, 'reject')}>
                                        {t('faro.device.reject', 'Reject')}
                                    </button>
                                </div>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <h3 className="skfaro-section-title">{t('faro.member.accessTitle', 'Access')}</h3>
            {grants.length === 0
                ? <p className="skfaro-muted">{t('faro.member.noAccess', 'No access yet. Give access to pick the servers this member can reach.')}</p>
                : (
                    <DataTable columns={grantColumns} data={grants} keyField="id"
                        storageKey="serverkit-table-faro-member-grants"
                        onRowClick={(g) => navigate(`/faro/grants/${g.id}`)} />
                )}

            <h3 className="skfaro-section-title">{t('faro.member.devicesTitle', 'Devices')}</h3>
            {shownDevices.length === 0
                ? <p className="skfaro-muted">{t('faro.member.noDevices', 'No devices yet. Add device creates a link the member opens in Faro on their laptop.')}</p>
                : <DataTable columns={deviceColumns} data={shownDevices} keyField="id" storageKey="serverkit-table-faro-devices" />}

            {activeInvites.length > 0 && (
                <>
                    <h3 className="skfaro-section-title">{t('faro.member.invitesTitle', 'Open add-device links')}</h3>
                    <ul className="skfaro-pending">
                        {activeInvites.map((invite) => (
                            <li key={invite.id}>
                                <span>
                                    {t('faro.member.inviteLine', 'Created {{created}}, expires {{expires}}', {
                                        created: when(invite.created_at), expires: when(invite.expires_at),
                                    })}
                                    {invite.require_approval && <span className="skfaro-muted"> · {t('faro.member.needsApproval', 'needs approval')}</span>}
                                </span>
                                <button type="button" className="btn btn-ghost btn-sm" disabled={busy !== null}
                                    onClick={() => act(`invite-${invite.id}`, () => faroGrantsApi.revokeInvite(invite.id),
                                        t('faro.member.inviteRevoked', 'Link revoked'))}>
                                    {t('faro.table.revoke', 'Revoke')}
                                </button>
                            </li>
                        ))}
                    </ul>
                </>
            )}

            {visibleKeys.length > 0 && (
                <>
                    <h3 className="skfaro-section-title">{t('faro.member.keysTitle', 'Where the keys are')}</h3>
                    <DataTable columns={keyColumns} data={visibleKeys} keyField="id" storageKey="serverkit-table-faro-member-keys" />
                </>
            )}

            <GrantCreateModal
                open={grantOpen}
                memberId={member.id}
                onClose={() => setGrantOpen(false)}
                onCreated={(grant, link, result) => {
                    setGrantOpen(false);
                    const failed = result?.failed || [];
                    if (failed.length) toast.warning(partialKeys(failed.length));
                    else toast.success(t('faro.create.memberSuccess', 'Access given to {{name}}', { name: member.name }));
                    load(true);
                }}
            />

            <Modal open={inviteOpen} onClose={() => busy === null && setInviteOpen(false)}
                title={t('faro.invite.title', 'Add a device for {{name}}', { name: member.name })}>
                <p>{t('faro.invite.intro', 'Creates a single-use link. Opened in Faro, it adds that device and gives it every server {{name}} has access to.', { name: member.name })}</p>
                <div className="skfaro-form__row">
                    <span className="form-label">{t('faro.invite.validFor', 'Link valid for')}</span>
                    <div className="skfaro-seg" role="radiogroup">
                        {TTL_PRESETS.slice(0, 4).map((preset) => (
                            <button key={preset.hours} type="button" role="radio"
                                aria-checked={inviteForm.ttlHours === preset.hours}
                                className={`btn btn-sm ${inviteForm.ttlHours === preset.hours ? 'btn-primary' : 'btn-ghost'}`}
                                onClick={() => setInviteForm({ ...inviteForm, ttlHours: preset.hours })}>
                                {t(`faro.ttl.${preset.key}`, preset.key)}
                            </button>
                        ))}
                    </div>
                </div>
                <label className="skfaro-check">
                    <input type="checkbox" checked={inviteForm.requireApproval}
                        onChange={(event) => setInviteForm({ ...inviteForm, requireApproval: event.target.checked })} />
                    <span>
                        {t('faro.invite.requireApproval', 'Require my approval before the device can connect')}
                        <span className="skfaro-form__hint skfaro-form__hint--block">
                            {t('faro.invite.requireApprovalHint', 'Recommended. If the link leaks, whoever opens it only creates a request you can reject.')}
                        </span>
                    </span>
                </label>
                <div className="skfaro-actions">
                    <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={createInvite}>
                        {busy === 'invite' ? t('faro.create.creating', 'Creating…') : t('faro.invite.create', 'Create link')}
                    </button>
                    <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => setInviteOpen(false)}>
                        {t('faro.create.cancel', 'Cancel')}
                    </button>
                </div>
            </Modal>

            <GrantLinkModal
                open={Boolean(issued)}
                link={issued?.link}
                expiresAt={issued?.invite?.expires_at}
                invite={issued?.invite}
                memberName={member.name}
                onClose={() => setIssued(null)}
            />

            <Modal open={Boolean(renaming)} onClose={() => busy === null && setRenaming(null)}
                title={t('faro.device.renameTitle', 'Rename device')}>
                <form onSubmit={(event) => { event.preventDefault(); saveRename(); }}>
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-device-label">{t('faro.device.label', 'Device')}</label>
                        <input id="skfaro-device-label" className="form-input" type="text" autoFocus maxLength={255}
                            value={renaming?.label || ''}
                            onChange={(event) => setRenaming({ ...renaming, label: event.target.value })} />
                    </div>
                    <div className="skfaro-actions">
                        <button type="submit" className="btn btn-primary" disabled={busy !== null}>{t('faro.device.save', 'Save')}</button>
                        <button type="button" className="btn btn-ghost" onClick={() => setRenaming(null)}>{t('faro.create.cancel', 'Cancel')}</button>
                    </div>
                </form>
            </Modal>
        </div>
    );
}
