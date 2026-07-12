import { useEffect, useState } from 'react';

/**
 * Runs an async `resolver` that turns a panel entity into a Faro connection
 * target `{ host, path, name, port?, username?, protocol? }` (or null when the
 * entity has no reachable host). Shared by every slot adapter so the
 * fetch/loading/fail-soft dance lives in one place.
 *
 * @param {() => Promise<object|null>} resolver
 * @param {Array} deps  re-run when these change (entity id, etc.)
 * @returns {{ target: object|null, loading: boolean, error: Error|null }}
 */
export default function useFaroTarget(resolver, deps = []) {
    const [state, setState] = useState({ target: null, loading: true, error: null });

    useEffect(() => {
        let cancelled = false;
        setState({ target: null, loading: true, error: null });
        Promise.resolve()
            .then(resolver)
            .then((target) => {
                if (!cancelled) setState({ target: target || null, loading: false, error: null });
            })
            .catch((error) => {
                if (!cancelled) setState({ target: null, loading: false, error });
            });
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, deps);

    return state;
}
