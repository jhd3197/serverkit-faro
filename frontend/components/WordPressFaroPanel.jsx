import useFaroTarget from '../hooks/useFaroTarget.js';
import { targetFromAppId } from '../lib/resolve.js';
import FaroConnectButton from './FaroConnectButton.jsx';

// Slot: wordpress.detail.panel — context { site }
// The WP site carries application_id; we resolve app → server for the SFTP host
// and use the site's own name as the connection label.
export default function WordPressFaroPanel({ context }) {
    const site = context?.site;
    const appId = site?.application_id;
    const { target, loading, error } = useFaroTarget(
        () => targetFromAppId(appId, { name: site?.name }),
        [appId, site?.name],
    );
    return <FaroConnectButton target={target} loading={loading} error={error} />;
}
