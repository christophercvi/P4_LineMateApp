/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Where `npm run dev` forwards /api calls (the FastAPI back-end started with `uv run back-end`).
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8000';

  return {
    plugins: [react()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host: '0.0.0.0',
      port: Number(env.VITE_PORT || 5173),
      strictPort: true,
      proxy: {
        // Server-sent events (Ask and Triage streams) pass straight through; no buffering.
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
    preview: {
      host: '0.0.0.0',
      port: Number(env.VITE_PREVIEW_PORT || 4173),
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
    build: {
      chunkSizeWarningLimit: 2600,
      reportCompressedSize: false,
      sourcemap: false,
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/test/setup.ts',
      css: false,
      testTimeout: 20000,
      hookTimeout: 20000,
      include: ['src/**/*.test.{ts,tsx}'],
      coverage: {
        provider: 'v8',
        reporter: ['text-summary', 'text', 'html', 'json-summary'],
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/test/**', 'src/**/*.test.{ts,tsx}', 'src/main.tsx', 'src/vite-env.d.ts'],
        thresholds: { statements: 70, branches: 60, functions: 60, lines: 70 },
      },
    },
  };
});
