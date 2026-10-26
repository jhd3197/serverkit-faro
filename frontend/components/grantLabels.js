import { useTranslation } from 'serverkit-sdk';

/** Translated labels for grant and per-server key states. */
export function useGrantLabels() {
    const { t } = useTranslation();

    const status = {
        active: t('faro.status.active', 'Waiting'),
        redeeming: t('faro.status.redeeming', 'Installing key'),
        redeemed: t('faro.status.redeemed', 'Live'),
        revoked: t('faro.status.revoked', 'Revoked'),
        expired: t('faro.status.expired', 'Expired'),
    };
    const health = {
        partial: t('faro.status.partial', 'Live, some servers failed'),
        cleanup: t('faro.status.cleanup', 'Key still on a server'),
    };
    const key = {
        pending: t('faro.key.pending', 'Not installed'),
        installed: t('faro.key.installed', 'Installed'),
        failed: t('faro.key.failed', 'Install failed'),
        removed: t('faro.key.removed', 'Removed'),
        remove_failed: t('faro.key.removeFailed', 'Removal failed'),
    };

    return {
        grant: (grant) => health[grant?.health] || status[grant?.status] || grant?.status || '—',
        key: (value) => key[value] || value || '—',
    };
}
