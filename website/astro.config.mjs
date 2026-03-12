// @ts-check
import { defineConfig } from 'astro/config';
import fs from 'node:fs';
import path from 'node:path';
import { gzipSync } from 'node:zlib';

// Vite plugin to serve .json.gz files from public/ in dev mode.
// Without this, the catch-all [...slug].astro route intercepts requests
// for /games/*.json.gz before the static file server can handle them,
// returning 503 instead of the actual file.
//
// Also adds transparent gzip compression for all compressible responses
// (JSON, JS, CSS, HTML, SVG). Game JSON files are 3-17 MB uncompressed but
// compress to ~3% of their size, so this is a massive dev-mode speedup.
/** @returns {import('vite').Plugin} */
function serveGzPlugin() {
  const COMPRESSIBLE = /\b(json|javascript|html|css|svg|xml|text)\b/i;
  const MIN_SIZE = 1024;

  /**
   * @param {string | Buffer | Uint8Array} chunk
   * @param {BufferEncoding | undefined} encoding
   * @returns {Buffer}
   */
  function toBuffer(chunk, encoding) {
    if (Buffer.isBuffer(chunk)) return chunk;
    if (typeof chunk === 'string') return Buffer.from(chunk, encoding);
    return Buffer.from(chunk);
  }

  return {
    name: 'serve-gz',
    /** @param {import('vite').ViteDevServer} server */
    configureServer(server) {
      /**
       * @param {import('node:http').IncomingMessage} req
       * @param {import('node:http').ServerResponse} res
       * @param {() => void} next
       */
      server.middlewares.use((req, res, next) => {
        // Serve pre-compressed .json.gz files directly (client decompresses).
        if (req.url && req.url.endsWith('.json.gz')) {
          const filePath = path.join(process.cwd(), 'public', req.url);
          if (fs.existsSync(filePath)) {
            res.setHeader('Content-Type', 'application/gzip');
            res.setHeader('Content-Encoding', 'identity');
            fs.createReadStream(filePath).pipe(res);
            return;
          }
        }

        // Skip compression for SSE (HMR) and clients that don't accept gzip.
        if (
          req.headers.accept === 'text/event-stream' ||
          !(req.headers['accept-encoding'] || '').includes('gzip')
        ) {
          return next();
        }

        // Wrap the response to buffer data and compress before sending.
        const _writeHead = res.writeHead;
        const _write = res.write;
        const _end = res.end;
        const rawEnd = /** @type {any} */ (_end);
        const mutableRes = /** @type {any} */ (res);
        /** @type {Buffer[]} */
        const chunks = [];
        let ended = false;

        // Intercept writeHead: apply headers via setHeader() so we can still
        // modify them (add Content-Encoding) before the response is flushed.
        /**
         * @param {any} statusCode
         * @param {any} statusMessage
         * @param {any} headers
         */
        mutableRes.writeHead = function (statusCode, statusMessage, headers) {
          if (typeof statusMessage === 'object' && statusMessage !== null) {
            headers = statusMessage;
            statusMessage = undefined;
          }
          if (headers) {
            for (const [key, value] of Object.entries(headers)) {
              if (value !== undefined) {
                try { res.setHeader(key, value); } catch {}
              }
            }
          }
          res.statusCode = statusCode;
          if (statusMessage) res.statusMessage = /** @type {string} */ (statusMessage);
          return res;
        };

        /**
         * @param {any} chunk
         * @param {any} encoding
         * @param {any} cb
         */
        mutableRes.write = function (chunk, encoding, cb) {
          if (chunk != null) {
            chunks.push(
              toBuffer(
                chunk,
                typeof encoding === 'string'
                  ? /** @type {BufferEncoding} */ (encoding)
                  : undefined,
              ),
            );
          }
          if (typeof encoding === 'function') encoding(undefined);
          else if (typeof cb === 'function') cb(undefined);
          return true;
        };

        /**
         * @param {any} chunk
         * @param {any} encoding
         * @param {any} cb
         */
        mutableRes.end = function (chunk, encoding, cb) {
          if (ended) return res;
          ended = true;

          if (chunk != null && typeof chunk !== 'function') {
            chunks.push(
              toBuffer(
                chunk,
                typeof encoding === 'string'
                  ? /** @type {BufferEncoding} */ (encoding)
                  : undefined,
              ),
            );
          }
          const callback = typeof chunk === 'function' ? chunk : typeof encoding === 'function' ? encoding : cb;

          const body = Buffer.concat(chunks);
          const ct = String(res.getHeader('content-type') || '');
          const alreadyEncoded = res.getHeader('content-encoding');

          // Restore original methods before calling _end, because Node's
          // end() internally calls this.writeHead() to flush headers.
          res.writeHead = _writeHead;
          res.write = _write;
          res.end = _end;

          if (body.length >= MIN_SIZE && COMPRESSIBLE.test(ct) && !alreadyEncoded) {
            const compressed = gzipSync(body);
            if (compressed.length < body.length) {
              res.setHeader('content-encoding', 'gzip');
              res.setHeader('vary', 'Accept-Encoding');
              res.removeHeader('content-length');
              return callback ? rawEnd.call(res, compressed, callback) : rawEnd.call(res, compressed);
            }
          }

          return callback ? rawEnd.call(res, body, callback) : rawEnd.call(res, body);
        };

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
/** @type {import('vite').ServerOptions['proxy']} */
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
      ...(auditProxy ? { proxy: auditProxy } : {}),
    },
  },
});
