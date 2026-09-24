import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 后端在随机端口，开发时用相对路径 + 这个代理
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    // 后端 StaticFiles 直接托管这个目录
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
});
