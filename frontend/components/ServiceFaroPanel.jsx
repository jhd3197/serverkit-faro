import useFaroTarget from '../hooks/useFaroTarget.js';
import { targetFromAppId } from '../lib/resolve.js';
import FaroConnectButton from './FaroConnectButton.jsx';

// Slot: service.detail.tab — context { serviceId }
export default function ServiceFaroPanel({ context }) {
    const serviceId = context?.serviceId;
    const { target, loading, error } = useFaroTarget(
        () => targetFromAppId(serviceId),
        [serviceId],
    );
    return <FaroConnectButton target={target} loading={loading} error={error} />;
}
