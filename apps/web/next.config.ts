import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // `standalone` keeps the production Docker image small: Next traces exactly
  // the files the server needs instead of shipping all of node_modules.
  output: "standalone",
  // The web container is one workspace inside a monorepo; without this Next
  // infers the wrong root and file tracing misses shared packages.
  outputFileTracingRoot: new URL("../../", import.meta.url).pathname,
  eslint: {
    // Lint is a separate CI step (`npm run lint`); running it inside `build`
    // makes a lint warning look like a build failure.
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
