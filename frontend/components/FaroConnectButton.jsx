import { buildFaroConnect } from '../lib/faroLink.js';
import { useTranslation } from 'serverkit-sdk';

// A small magnet glyph so the button reads as "Open in Faro" without pulling in
// an icon dependency (the panel externalizes React only).
function MagnetIcon() {
    return (
        <svg className="skfaro-btn__icon" width="15" height="15" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15" />
            <path d="m5 8 4 4" />
            <path d="m12 15 4 4" />
        </svg>
    );
}

/**
 * Presentational "Open in Faro" panel. Given a resolved `target`, renders the
 * connect button + a preview of exactly what Faro will be pointed at. Fail-soft:
 * loading, error, and no-host states each render an unobtrusive note instead of
 * a broken link.
 *
 * @param {object} props
 * @param {object|null} props.target   { host, path, name, port?, username?, protocol? }
 * @param {boolean} props.loading
 * @param {Error|null} props.error
 */
export default function FaroConnectButton({ target, loading, error }) {
    const { t } = useTranslation();
    const url = target ? buildFaroConnect(target) : null;

    return (
        <section className="skfaro">
            <div className="skfaro__head">
                <MagnetIcon />
                <span className="skfaro__title">{t('faro.faroConnectButton.faro', 'Faro')}</span>
            </div>

            {loading && <p className="skfaro__note">{t('faro.faroConnectButton.resolvingConnectionDetails', 'Resolving connection details…')}</p>}

            {!loading && error && (
                <p className="skfaro__note skfaro__note--muted">
                    {t('faro.faroConnectButton.couldnTLoadConnectionDetailsFor', 'Couldn’t load connection details for this item.')}
                </p>
            )}

            {!loading && !error && !url && (
                <p className="skfaro__note skfaro__note--muted">
                    {t('faro.faroConnectButton.noReachableHostForThisItem', 'No reachable host for this item yet — Faro connect is unavailable.')}
                </p>
            )}

            {!loading && !error && url && (
                <>
                    <a className="skfaro-btn" href={url}>
                        <MagnetIcon />
                        {t('faro.faroConnectButton.openInFaro', 'Open in Faro')}
                    </a>
                    <p className="skfaro__hint">
                        {t('faro.faroConnectButton.opensFaroSConnectionEditorPrefilled', 'Opens Faro’s connection editor prefilled for')}
                        {' '}<code>{target.host}</code>
                        {target.path ? <> at <code>{target.path}</code></> : null}.
                        {' '}{t('faro.faroConnectButton.noPasswordIsSentYouReview', 'No password is sent — you review and connect in Faro.')}
                    </p>
                </>
            )}
        </section>
    );
}
