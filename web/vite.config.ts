import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: { target: 'es2022', chunkSizeWarningLimit: 2000 },
  // MapLibre 6 loads its worker by a URL relative to its own module; pre-bundling breaks that.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: { port: 5190, strictPort: true },
  preview: { port: 5190, strictPort: true },
});
