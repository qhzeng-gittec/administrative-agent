/** @type {import('next').NextConfig} */
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  experimental: {
    // LLM 问答可能超过默认 30s 代理超时，放宽到 3 分钟
    proxyTimeout: 180000,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/healthz", destination: `${BACKEND_URL}/healthz` },
      { source: "/metrics", destination: `${BACKEND_URL}/metrics` },
    ];
  },
};

export default nextConfig;
