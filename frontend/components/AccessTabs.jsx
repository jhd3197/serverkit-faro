import { Link, useLocation, useTranslation } from 'serverkit-sdk';

/** Access | Members switcher shown at the top of both list pages. */
export default function AccessTabs({ pendingDevices = 0 }) {
    const { t } = useTranslation();
    const { pathname } = useLocation();
    const onMembers = pathname.startsWith('/faro/members');
    return (
        <nav className="skfaro-tabs" aria-label={t('faro.tabs.label', 'Access sections')}>
            <Link to="/faro" className={`skfaro-tabs__tab${onMembers ? '' : ' is-active'}`}
                aria-current={onMembers ? undefined : 'page'}>
                {t('faro.tabs.grants', 'Access')}
            </Link>
            <Link to="/faro/members" className={`skfaro-tabs__tab${onMembers ? ' is-active' : ''}`}
                aria-current={onMembers ? 'page' : undefined}>
                {t('faro.tabs.members', 'Members')}
                {pendingDevices > 0 && <span className="skfaro-tabs__count">{pendingDevices}</span>}
            </Link>
        </nav>
    );
}
