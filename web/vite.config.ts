import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev-only proxy: in production this app is built into src/bmem/static and
// served by the same FastAPI process, so /api is same-origin and needs no
// proxy at all. In dev, Vite runs on its own port, so /api and the SSE
// stream both have to be forwarded to the real backend on :8000.
export default defineConfig(({ command }) => ({
  // Build-time only. The production bundle is served by FastAPI from a
  // StaticFiles mount at /static -- mounted there rather than at / so a
  // catch-all static prefix can never shadow the /api routes. Under the
  // default base of "/", the emitted index.html asked for
  // /assets/index-*.js, which that mount does not serve: the page returned
  // 200 and then rendered nothing.
  //
  // Applying it in dev too would be wrong in the other direction -- Vite
  // would move the dev server's own app to /static/, so the documented
  // http://localhost:5173/?root=<id> would stop being the right URL.
  base: command === "build" ? "/static/" : "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
}))
