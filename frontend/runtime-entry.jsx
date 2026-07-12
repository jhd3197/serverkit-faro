// Runtime-ESM entry for ServerKit's no-rebuild loader (panel plan 25).
//
// CSS is imported as a STRING (?inline) and injected once at module load, so
// the single dist/index.mjs the panel blob-imports carries its own styles —
// no separate .css asset the runtime loader wouldn't fetch. Shared libs
// (react, serverkit-sdk) are externalized by vite.config and resolved to the
// panel's singletons via its import map.
import css from './styles/faro.css?inline';
import ServiceFaroPanel from './components/ServiceFaroPanel.jsx';
import DomainFaroPanel from './components/DomainFaroPanel.jsx';
import WordPressFaroPanel from './components/WordPressFaroPanel.jsx';

if (typeof document !== 'undefined' && !document.getElementById('serverkit-faro-styles')) {
    const style = document.createElement('style');
    style.id = 'serverkit-faro-styles';
    style.textContent = css;
    document.head.appendChild(style);
}

// Named exports match the `component` values in plugin.json's widget
// contributions; resolveComponent(slug, name) picks them up at runtime.
export { ServiceFaroPanel, DomainFaroPanel, WordPressFaroPanel };
