import useFaroTarget from '../hooks/useFaroTarget.js';
import { targetFromAppId } from '../lib/resolve.js';
import FaroConnectButton from './FaroConnectButton.jsx';

// Slot: domain.drawer.panel — context { domain }
// Provider-only domains (no attached app) resolve to null → "unavailable" note.
export default function DomainFaroPanel({ context }) {
    const domain = context?.domain;
    const appId = domain?.application_id;
    const { target, loading, error } = useFaroTarget(
        () => targetFromAppId(appId, { name: domain?.name }),
        [appId, domain?.name],
    );
    return <FaroConnectButton target={target} loading={loading} error={error} />;
}
