import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// ServerKit runtime-ESM extension build (panel plan 25).
// Externalizes the host-shared libraries so the panel resolves them to its OWN
// singletons via the import map — never bundle React (a second copy crashes
// hooks). Emits one self-contained dist/index.mjs; CSS is inlined via the
// runtime entry's `?inline` import, so there is no separate stylesheet asset.
const EXTERNAL = [
    'react', 'react-dom', 'react-dom/client', 'react/jsx-runtime',
    'react-router-dom', 'serverkit-sdk',
];

export default defineConfig({
    plugins: [react()],
    build: {
        outDir: 'dist',
        emptyOutDir: false, // dist also holds release zips; keep them
        lib: {
            entry: 'runtime-entry.jsx',
            formats: ['es'],
            fileName: () => 'index.mjs',
        },
        rollupOptions: {
            external: EXTERNAL,
            output: { inlineDynamicImports: true },
        },
    },
});
