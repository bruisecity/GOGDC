import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发态代理：/api 与 /stac 转发到后端 FastAPI（默认 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/stac": "http://localhost:8000",
    },
  },
  build: { outDir: "dist" },
  preview: {
    proxy: {
      "/api": "http://localhost:8000",
      "/stac": "http://localhost:8000",
    },
  },
});
