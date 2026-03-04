// @ts-check
import { defineConfig } from 'astro/config';
import fs from 'node:fs';
import path from 'node:path';

// Vite plugin to serve .json.gz files from public/ in dev mode.
// Without this, the catch-all [...slug].astro route intercepts requests
// for /games/*.json.gz before the static file server can handle them,
// returning 503 instead of the actual file.
function serveGzPlugin() {
  return {
    name: 'serve-gz',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url && req.url.endsWith('.json.gz')) {
          const filePath = path.join(process.cwd(), 'public', req.url);
          if (fs.existsSync(filePath)) {
            res.setHeader('Content-Type', 'application/gzip');
            res.setHeader('Content-Encoding', 'identity');
            fs.createReadStream(filePath).pipe(res);
            return;
          }
        }
        next();
      });
    },
  };
}

// Load hostname from config for Vite's allowedHosts (remote access via tunnel/DNS).
// Config file only exists on dev machines, not in CI.
const configPath = path.join(process.env.HOME || '', '.mage-bench', 'config.json');
const allowedHosts = fs.existsSync(configPath)
  ? [JSON.parse(fs.readFileSync(configPath, 'utf-8')).hostname]
  : [];

// Proxy audit API to the Python backend when running in audit mode.
// AUDIT_API_PORT is set by `make blunder-audit-web`.
const auditApiPort = process.env.AUDIT_API_PORT;
const auditProxy = auditApiPort
  ? {
      '/api/audit': {
        target: `http://localhost:${auditApiPort}`,
        rewrite: (/** @type {string} */ p) => p.replace(/^\/api\/audit/, '/api'),
      },
    }
  : {};

// https://astro.build/config
export default defineConfig({
  site: 'https://mage-bench.com',
  vite: {
    plugins: [serveGzPlugin()],
    server: {
      allowedHosts,
      proxy: auditProxy,
    },
  },
});
