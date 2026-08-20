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

// Translations. Registered against the PANEL's i18next singleton (shared via
// its vendor import map), additively and under this extension's own
// `faro` namespace — never init() or changeLanguage(), which the panel
// owns and which would reconfigure or switch the language everywhere.
//
// The English bundle is generated from the inline t('key', 'English')
// defaults, so a key with no bundle still renders its default. More locales
// drop in beside en.json with one addResourceBundle line each.
import i18next from 'i18next';
import en from './locales/en.json';

for (const [language, bundle] of Object.entries({ en })) {
    i18next.addResourceBundle(language, 'translation', bundle, true, false);
}


if (typeof document !== 'undefined' && !document.getElementById('serverkit-faro-styles')) {
    const style = document.createElement('style');
    style.id = 'serverkit-faro-styles';
    style.textContent = css;
    document.head.appendChild(style);
}

// Named exports match the `component` values in plugin.json's widget
// contributions; resolveComponent(slug, name) picks them up at runtime.
export { ServiceFaroPanel, DomainFaroPanel, WordPressFaroPanel };
