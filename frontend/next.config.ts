import type { NextConfig } from "next";

// 1. Properly import the i18n configuration
const { i18n } = require("./next-i18next.config.js");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  i18n,
};

// 2. Export the config directly to bypass the missing PWA dependencies
export default nextConfig;