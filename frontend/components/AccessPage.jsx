import { useCallback, useEffect, useState } from 'react';
import {
    DataTable,
    Pill,
    emitWalkthroughSignal,
    formatRelativeTime,
    useConfirm,
    useLocation,
    useNavigate,
    useToast,
    useTranslation,
} from 'serverkit-sdk';
import { faroGrantsApi } from '../services/faroGrants.js';
import { grantKind, isOpen, summarize } from '../lib/grants.js';
import GrantCreateModal from './GrantCreateModal.jsx';
import GrantLinkModal from './GrantLinkModal.jsx';
import { useGrantLabels } from './grantLabels.js';

/**
 * /faro — every access grant: who it's for, which servers, whether the key is
 * live, and when it ends. "New access" issues a faro://grant link;
 * `?share=<serverId>` (from the Faro panels on service/domain/WordPress
 * pages) opens the form with that server already picked.
 */
export default function AccessPage() {
    const { t } = useTranslation();
    const toast = useToast();
    const { confirm } = useConfirm();
    const navigate = useNavigate();
    const location = useLocation();
    const labels = useGrantLabels();

    const [grants, setGrants] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [busyId, setBusyId] = useState(null);

    const [createOpen, setCreateOpen] = useState(false);
    const [preselect, setPreselect] = useState(null);
    const [issued, setIssued] = useState(null); // { grant, link }

    const load = useCallback(async (background = false) => {
        if (!background) setLoading(true);
        try {
            const data = await faroGrantsApi.listGrants();
            setGrants(Array.isArray(data?.grants) ? data.grants : []);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    // Deep link from a Faro panel: /faro?share=<serverId>.
    useEffect(() => {
        const share = new URLSearchParams(location.search).get('share');
        if (share) {
            setPreselect(share);
            setCreateOpen(true);
            navigate('/faro', { replace: true });
        }
    }, [location.search, navigate]);

    // Waiting links flip to redeemed when the person accepts; poll gently
    // while any are outstanding so the admin sees it happen.
    const anyWaiting = grants.some((grant) => grant.status === 'active' || grant.status === 'redeeming');
    useEffect(() => {
        if (!anyWaiting) return undefined;
        const timer = setInterval(() => load(true), 15000);
        return () => clearInterval(timer);
    }, [anyWaiting, load]);

    const openCreate = () => {
        setPreselect(null);
        setCreateOpen(true);
    };

    const handleCreated = (grant, link) => {
        setCreateOpen(false);
        setIssued({ grant, link });
        emitWalkthroughSignal('faro.grant-created', { id: grant?.id });
        load(true);
    };

    const handleRevoke = async (grant) => {
        const ok = await confirm({
            title: t('faro.revoke.title', 'Revoke "{{name}}"?', { name: grant.name }),
            message: t('faro.revoke.message', 'The link stops working and the key is removed from every server it was installed on. Faro connections using it start failing right away.'),
            confirmText: t('faro.revoke.confirm', 'Revoke access'),
            variant: 'danger',
        });
        if (!ok) return;
        setBusyId(grant.id);
        try {
            const result = await faroGrantsApi.revokeGrant(grant.id);
            if (result?.failed?.length) {
                toast.warning(t('faro.revoke.partial', 'Revoked, but the key could not be removed from {{count}} server(s) yet. It will keep retrying.', { count: result.failed.length }));
            } else {
                toast.success(t('faro.revoke.success', 'Access revoked'));
            }
            await load(true);
        } catch (err) {
            toast.error(err.message);
        } finally {
            setBusyId(null);
        }
    };

    const counts = summarize(grants);

    const columns = [
        {
            key: 'name',
            header: t('faro.table.name', 'Name'),
            sortable: true,
            hideable: false,
            render: (grant) => <strong>{grant.name}</strong>,
        },
        {
            key: 'servers',
            header: t('faro.table.servers', 'Servers'),
            sortable: true,
            sortValue: (grant) => (grant.servers || []).length,
            render: (grant) => {
                const names = (grant.servers || []).map((s) => s.name);
                const shown = names.slice(0, 2).join(', ');
                return names.length > 2
                    ? <span title={names.join(', ')}>{t('faro.table.serversMore', '{{shown}} +{{count}} more', { shown, count: names.length - 2 })}</span>
                    : shown || '—';
            },
        },
        {
            key: 'ssh_username',
            header: t('faro.table.login', 'Login'),
            sortable: true,
            render: (grant) => <code>{grant.ssh_username}</code>,
        },
        {
            key: 'bridge',
            header: t('faro.table.bridge', 'Bridge'),
            sortable: true,
            sortValue: (grant) => grant.jump?.name || grant.jump?.host || '',
            render: (grant) => grant.jump ? (grant.jump.name || grant.jump.host) : '—',
        },
        {
            key: 'status',
            header: t('faro.table.status', 'Status'),
            sortable: true,
            render: (grant) => <Pill kind={grantKind(grant)}>{labels.grant(grant)}</Pill>,
        },
        {
            key: 'expires_at',
            header: t('faro.table.expires', 'Ends'),
            sortable: true,
            render: (grant) => (isOpen(grant) && grant.expires_at
                ? formatRelativeTime(grant.expires_at)
                : '—'),
        },
        {
            key: 'created_at',
            header: t('faro.table.created', 'Created'),
            sortable: true,
            render: (grant) => (grant.created_at ? formatRelativeTime(grant.created_at) : '—'),
        },
        {
            key: 'actions',
            header: '',
            sortable: false,
            hideable: false,
            className: 'text-right',
            render: (grant) => (
                <div className="skfaro-table__actions" onClick={(event) => event.stopPropagation()}>
                    {isOpen(grant) && (
                        <button
                            type="button"
                            className="btn btn-danger btn-sm"
                            disabled={busyId === grant.id}
                            onClick={() => handleRevoke(grant)}
                        >
                            {t('faro.table.revoke', 'Revoke')}
                        </button>
                    )}
                </div>
            ),
        },
    ];

    return (
        <div className="skfaro-page">
            <div className="skfaro-page__header">
                <div>
                    <h2 className="skfaro-page__title">{t('faro.page.title', 'Access')}</h2>
                    <p className="skfaro-muted">
                        {t('faro.page.subtitle', 'Give someone time-boxed SSH and SFTP access to your servers with one Faro link. No passwords change hands, and revoking removes the key.')}
                    </p>
                </div>
                <div className="skfaro-actions skfaro-actions--tight">
                    <button type="button" className="btn btn-ghost" onClick={() => load()}>
                        {t('faro.page.refresh', 'Refresh')}
                    </button>
                    <button
                        type="button"
                        className="btn btn-primary"
                        data-walkthrough="faro-new-access"
                        onClick={openCreate}
                    >
                        {t('faro.page.new', 'New access')}
                    </button>
                </div>
            </div>

            {!loading && !error && grants.length > 0 && (
                <p className="skfaro-summary">
                    {t('faro.page.summary', '{{live}} live · {{waiting}} waiting to be accepted', counts)}
                    {counts.attention > 0 && (
                        <> · <span className="skfaro-summary__warn">{t('faro.page.attention', '{{count}} need attention', { count: counts.attention })}</span></>
                    )}
                </p>
            )}

            {loading && <p className="skfaro-muted">{t('faro.page.loading', 'Loading access…')}</p>}
            {!loading && error && <p className="skfaro-form__error">{error}</p>}

            {!loading && !error && grants.length === 0 && (
                <div className="skfaro-empty">
                    <h3>{t('faro.empty.title', 'No one has access yet')}</h3>
                    <p className="skfaro-muted">
                        {t('faro.empty.message', 'Create a link for a teammate or contractor. They open it in Faro, accept, and get SFTP and terminal access to the servers you picked until it ends or you revoke it.')}
                    </p>
                    <button type="button" className="btn btn-primary" data-walkthrough="faro-new-access" onClick={openCreate}>
                        {t('faro.page.new', 'New access')}
                    </button>
                </div>
            )}

            {!loading && !error && grants.length > 0 && (
                <DataTable
                    columns={columns}
                    data={grants}
                    keyField="id"
                    storageKey="serverkit-table-faro-grants"
                    onRowClick={(grant) => navigate(`/faro/grants/${grant.id}`)}
                    keyboardNav
                />
            )}

            <GrantCreateModal
                open={createOpen}
                preselectServerId={preselect}
                onClose={() => setCreateOpen(false)}
                onCreated={handleCreated}
            />
            <GrantLinkModal
                open={Boolean(issued)}
                link={issued?.link}
                grant={issued?.grant}
                onClose={() => setIssued(null)}
            />
        </div>
    );
}
