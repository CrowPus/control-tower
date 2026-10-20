import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// The admin app, served by app/main.py in production; Vite serves it in development.
export default defineConfig({
  root: path.resolve(import.meta.dirname, "admin"),
  plugins: [react(), tailwindcss()],
  build: { outDir: path.resolve(import.meta.dirname, "dist-admin"), emptyOutDir: true },
  server: {
    host: process.env.HOST || "127.0.0.1",
    port: Number(process.env.ADMIN_PORT) || 5174,
    strictPort: true,
    proxy: { "/admin-api": { target: process.env.ADMIN_API_TARGET || "http://127.0.0.1:8000" } },
  },
});
