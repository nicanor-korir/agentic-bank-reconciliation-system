import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server runs inside the `web` container with ./web/src bind-mounted,
// so file watching needs polling to see host-side edits.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: { usePolling: true },
  },
});
