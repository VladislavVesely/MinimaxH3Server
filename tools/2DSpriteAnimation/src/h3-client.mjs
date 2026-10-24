import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import https from 'node:https';
import tls from 'node:tls';
import crypto from 'node:crypto';

export const configPath = () => path.join(process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent'), 'h3-studio.json');
export function readConfig(file = configPath()) {
  if (arguments.length > 1) throw new Error('h3_config_invalid');
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const st = fs.fstatSync(fd);
    if (!st.isFile() || st.nlink !== 1 || st.size > 65536 || (process.getuid && (st.uid !== process.getuid() || st.mode & 0o077))) throw new Error('private');
    const c = JSON.parse(fs.readFileSync(fd, 'utf8'));
    const keys = ['version', 'host', 'port', 'apiKey', 'certificatePem', 'certificateSha256'];
    if (Object.keys(c).sort().join() !== keys.sort().join() || c.version !== 1 || !net.isIP(c.host) || !Number.isInteger(c.port) || c.port < 1024 || c.port > 65535 || typeof c.apiKey !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(c.apiKey) || typeof c.certificatePem !== 'string' || !/^[a-f0-9]{64}$/.test(c.certificateSha256)) throw new Error('config');
    const pin = new crypto.X509Certificate(c.certificatePem).fingerprint256.replaceAll(':', '').toLowerCase();
    if (pin !== c.certificateSha256) throw new Error('config');
    return c;
  } catch (e) {
    throw new Error(e.message === 'private' ? 'h3_private_configuration_required' : 'h3_config_invalid_or_missing');
  } finally { if (fd !== undefined) fs.closeSync(fd); }
}
export class Client {
  constructor(config) { this.config = config; }
  redact(value) {
    if (typeof value === 'string') return value.replaceAll(this.config.apiKey, '[redacted]');
    if (Array.isArray(value)) return value.map(v => this.redact(v));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [this.redact(k), this.redact(v)]));
    return value;
  }
  async request(relative, { method = 'GET', body, signal, binary = false, confirmed = false, limit = 4 * 1024 * 1024, timeout = 45000 } = {}) {
    if (typeof relative !== 'string' || !/^\/(?:[a-z0-9_\/-]+)(?:\?[^#]*)?$/.test(relative) || relative.includes('..') || relative.startsWith('//') || !['GET', 'POST', 'DELETE'].includes(method)) throw new Error('h3_api_path_invalid');
    const c = this.config;
    const payload = body === undefined ? null : Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body));
    if (payload && payload.length > 64 * 1024 * 1024) throw new Error('h3_upload_limit');
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (e, value) => { if (!settled) { settled = true; e ? reject(e) : resolve(value); } };
      const req = https.request({ hostname: c.host, port: c.port, path: '/api/v1/agent' + relative, method, signal, ca: c.certificatePem, rejectUnauthorized: true, agent: false,
        checkServerIdentity: (host, cert) => {
          const error = tls.checkServerIdentity(host, cert);
          if (error) return error;
          if (!cert.raw || crypto.createHash('sha256').update(cert.raw).digest('hex') !== c.certificateSha256) return new Error('h3_tls_identity_changed');
        },
        headers: { Authorization: 'Bearer ' + c.apiKey, ...(confirmed ? { 'X-H3-Confirm': 'yes' } : {}), ...(payload ? { 'Content-Type': Buffer.isBuffer(body) ? 'application/octet-stream' : 'application/json', 'Content-Length': payload.length } : {}) }
      }, res => {
        const parts = []; let bytes = 0;
        res.on('data', chunk => { bytes += chunk.length; if (bytes > limit) { finish(new Error('h3_response_limit')); res.destroy(); req.destroy(); } else parts.push(chunk); });
        res.on('error', () => finish(new Error('h3_response_interrupted_no_replay')));
        res.on('end', () => {
          const status = res.statusCode || 503, data = Buffer.concat(parts);
          if (status >= 300 && status < 400) return finish(new Error('h3_redirect_forbidden'));
          if (status < 200 || status >= 300) {
            let code = 'h3_api_failure';
            try { const parsed = JSON.parse(data); const candidate = parsed.error || parsed.detail; if (typeof candidate === 'string' && /^[a-z][a-z0-9_]{0,95}$/.test(candidate)) code = candidate; } catch {}
            return finish(new Error(this.redact(`H3 ${status}: ${code}. No automatic mutation replay.`)));
          }
          if (binary) return finish(null, { data, contentType: String(res.headers['content-type'] || '').split(';')[0], status });
          try { finish(null, { data: this.redact(JSON.parse(data)), status }); } catch { finish(new Error('h3_response_invalid')); }
        });
      });
      req.setTimeout(timeout, () => { finish(new Error('h3_connection_timeout_no_replay')); req.destroy(); });
      req.on('error', () => finish(new Error(signal?.aborted ? 'h3_request_aborted_no_replay' : 'h3_connection_failed_no_replay')));
      req.end(payload);
    });
  }
}
