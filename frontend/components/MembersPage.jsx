import { useCallback, useEffect, useState } from 'react';
import {
    DataTable,
    Modal,
    Pill,
    formatRelativeTime,
    useNavigate,
    useToast,
    useTranslation,
} from 'serverkit-sdk';
import { faroGrantsApi } from '../services/faroGrants.js';
import AccessTabs from './AccessTabs.jsx';

const EMPTY = { name: '', email: '', note: '' };

/**
 * /faro/members — the people who have (or had) access. A member owns grants
 * and devices: each device they add from an invite link gets every server
 * their live grants cover.
 */
export default function MembersPage() {
    const { t } = useTranslation();
    const toast = useToast();
    const navigate = useNavigate();

    const [members, setMembers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);
    const [form, setForm] = useState(EMPTY);
    const [saving, setSaving] = useState(false);

    const load = useCallback(async (background = false) => {
        if (!background) setLoading(true);
        try {
            const data = await faroGrantsApi.listMembers();
            setMembers(Array.isArray(data?.members) ? data.members : []);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const create = async () => {
        if (!form.name.trim()) {
            toast.error(t('faro.members.nameRequired', 'A name is required'));
            return;
        }
        setSaving(true);
        try {
            const result = await faroGrantsApi.createMember({
                name: form.name.trim(), email: form.email.trim(), note: form.note.trim(),
            });
            setCreateOpen(false);
            setForm(EMPTY);
            navigate(`/faro/members/${result.member.id}`);
        } catch (err) {
            toast.error(err?.data?.error || err.message);
        } finally {
            setSaving(false);
        }
    };

    const pending = members.reduce((sum, m) => sum + (m.devices_pending || 0), 0);

    const columns = [
        {
            key: 'name',
            header: t('faro.members.name', 'Name'),
            sortable: true,
            hideable: false,
            render: (m) => (
                <>
                    <strong>{m.name}</strong>
                    {m.email && <span className="skfaro-muted"> · {m.email}</span>}
                </>
            ),
        },
        {
            key: 'devices_active',
            header: t('faro.members.devices', 'Devices'),
            sortable: true,
            render: (m) => (
                <>
                    {m.devices_active}
                    {m.devices_pending > 0 && (
                        <>
                            {' '}
                            <Pill kind="amber">
                                {t('faro.members.pendingCount', '{{count}} to approve', { count: m.devices_pending })}
                            </Pill>
                        </>
                    )}
                </>
            ),
        },
        {
            key: 'grants_live',
            header: t('faro.members.access', 'Live access'),
            sortable: true,
        },
        {
            key: 'keys_failing',
            header: t('faro.members.health', 'Keys'),
            sortable: true,
            render: (m) => (m.keys_failing > 0
                ? <Pill kind="red">{t('faro.members.failing', '{{count}} failing', { count: m.keys_failing })}</Pill>
                : <span className="skfaro-muted">{t('faro.members.ok', 'OK')}</span>),
        },
        {
            key: 'last_device_at',
            header: t('faro.members.lastDevice', 'Last device added'),
            sortable: true,
            render: (m) => (m.last_device_at ? formatRelativeTime(m.last_device_at) : '—'),
        },
    ];

    return (
        <div className="skfaro-page">
            <AccessTabs pendingDevices={pending} />
            <div className="skfaro-page__header">
                <div>
                    <h2 className="skfaro-page__title">{t('faro.members.title', 'Members')}</h2>
                    <p className="skfaro-muted">
                        {t('faro.members.subtitle', 'People who get access on every device they add. Give a member access once, then send them an add-device link for each laptop.')}
                    </p>
                </div>
                <div className="skfaro-actions skfaro-actions--tight">
                    <button type="button" className="btn btn-primary" onClick={() => setCreateOpen(true)}>
                        {t('faro.members.new', 'New member')}
                    </button>
                </div>
            </div>

            {loading && <p className="skfaro-muted">{t('faro.members.loading', 'Loading members…')}</p>}
            {!loading && error && <p className="skfaro-form__error">{error}</p>}
            {!loading && !error && members.length === 0 && (
                <div className="skfaro-empty">
                    <h3>{t('faro.members.emptyTitle', 'No members yet')}</h3>
                    <p className="skfaro-muted">
                        {t('faro.members.emptyBody', 'Add a teammate or contractor as a member when they will need access more than once, or from more than one device.')}
                    </p>
                    <button type="button" className="btn btn-primary" onClick={() => setCreateOpen(true)}>
                        {t('faro.members.new', 'New member')}
                    </button>
                </div>
            )}
            {!loading && !error && members.length > 0 && (
                <DataTable
                    columns={columns}
                    data={members}
                    keyField="id"
                    storageKey="serverkit-table-faro-members"
                    onRowClick={(m) => navigate(`/faro/members/${m.id}`)}
                    keyboardNav
                />
            )}

            <Modal open={createOpen} onClose={() => !saving && setCreateOpen(false)}
                title={t('faro.members.new', 'New member')}>
                <form onSubmit={(event) => { event.preventDefault(); create(); }}>
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-m-name">{t('faro.members.name', 'Name')}</label>
                        <input id="skfaro-m-name" className="form-input" type="text" autoFocus maxLength={255}
                            value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
                    </div>
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-m-email">{t('faro.members.email', 'Email (optional)')}</label>
                        <input id="skfaro-m-email" className="form-input" type="email" maxLength={255}
                            value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
                        <p className="skfaro-form__hint">{t('faro.members.emailHint', 'For your reference. ServerKit does not send email to members.')}</p>
                    </div>
                    <div className="skfaro-form__row">
                        <label className="form-label" htmlFor="skfaro-m-note">{t('faro.members.note', 'Note (optional)')}</label>
                        <textarea id="skfaro-m-note" className="form-input" rows={2} maxLength={2000}
                            value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} />
                    </div>
                    <div className="skfaro-actions">
                        <button type="submit" className="btn btn-primary" disabled={saving}>
                            {saving ? t('faro.members.creating', 'Adding…') : t('faro.members.create', 'Add member')}
                        </button>
                        <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => setCreateOpen(false)}>
                            {t('faro.create.cancel', 'Cancel')}
                        </button>
                    </div>
                </form>
            </Modal>
        </div>
    );
}
