import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const projectRoot = path.resolve(__dirname, "../..");
const sharedDashboardRoot = path.resolve(projectRoot, "src/dashboard");

export default defineConfig({
  root: __dirname,
  base: "/dashboard-app/",
  build: {
    emptyOutDir: true,
    outDir: path.resolve(projectRoot, "static/dashboard-app"),
  },
  envPrefix: ["VITE_", "REACT_APP_"],
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@dashboard": sharedDashboardRoot,
      react: path.resolve(__dirname, "./node_modules/react"),
      "react-dom": path.resolve(__dirname, "./node_modules/react-dom"),
      "react-router-dom": path.resolve(__dirname, "./node_modules/react-router-dom"),
      "@stripe/stripe-js": path.resolve(__dirname, "./node_modules/@stripe/stripe-js"),
      tailwindcss: path.resolve(__dirname, "./node_modules/tailwindcss/index.css"),
    },
  },
  server: {
    port: 5173,
    fs: {
      allow: ["../..", projectRoot, sharedDashboardRoot],
    },
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
});
