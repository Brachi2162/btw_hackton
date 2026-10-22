/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, /api goes to the FastAPI server; in Docker, nginx does the same.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.VITE_API ?? "http://localhost:8000" },
  },
  test: { environment: "jsdom", globals: false },
});
