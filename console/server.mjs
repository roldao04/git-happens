// Connected Home console: a local page + a tiny bridge to the MCP server.
// The bridge starts the same `connected-home` command that .mcp.json defines
// (mcp-remote, which owns the login and token refresh) and talks MCP over stdio.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 4780);
const CALL_TIMEOUT_MS = 60_000;

let child = null;
let ready = null; // Promise that resolves once the MCP session is initialized
let nextId = 1;
const pending = new Map();

async function start() {
  const cfg = JSON.parse(await readFile(join(here, '..', '.mcp.json'), 'utf8'));
  const def = cfg.mcpServers['connected-home'];
  child = spawn(def.command, def.args, { stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, ...def.env } });

  let buf = '';
  child.stdout.on('data', (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      const waiter = pending.get(msg.id);
      if (!waiter) continue;
      pending.delete(msg.id);
      clearTimeout(waiter.timer);
      if (msg.error) waiter.reject(new Error(msg.error.message || 'MCP error'));
      else waiter.resolve(msg.result);
    }
  });
  child.stderr.on('data', (d) => process.stderr.write(d));
  child.on('exit', (code) => {
    console.error(`[console] MCP bridge exited (${code})`);
    child = null;
    ready = null;
    for (const w of pending.values()) { clearTimeout(w.timer); w.reject(new Error('MCP connection closed')); }
    pending.clear();
  });

  await rpc('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'connected-home-console', version: '1.0.0' },
  });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
}

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    if (!child) return reject(new Error('MCP bridge is not running'));
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timed out after ${CALL_TIMEOUT_MS / 1000}s`));
    }, CALL_TIMEOUT_MS);
    pending.set(id, { resolve, reject, timer });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
}

function connect() {
  if (!ready) ready = start().catch((e) => { ready = null; child?.kill(); throw e; });
  return ready;
}

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1_000_000) throw new Error('Request too large');
  }
  return raw ? JSON.parse(raw) : {};
}

const LOCAL = new Set([`localhost:${PORT}`, `127.0.0.1:${PORT}`]);

createServer(async (req, res) => {
  // Local use only: refuse other hosts and cross-site requests.
  const origin = req.headers.origin;
  if (!LOCAL.has(req.headers.host) || (origin && !LOCAL.has(origin.replace(/^https?:\/\//, '')))) {
    return send(res, 403, { error: 'Forbidden' });
  }
  try {
    if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      return res.end(await readFile(join(here, 'index.html')));
    }
    if (req.method === 'GET' && req.url === '/api/tools') {
      await connect();
      return send(res, 200, await rpc('tools/list', {}));
    }
    if (req.method === 'POST' && req.url === '/api/call') {
      const { name, arguments: args } = await readBody(req);
      if (typeof name !== 'string') return send(res, 400, { error: 'Missing tool name' });
      await connect();
      const result = await rpc('tools/call', { name, arguments: args || {} });
      const text = (result.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n');
      let data = text;
      try { data = JSON.parse(text); } catch { /* plain text result */ }
      return send(res, 200, { isError: !!result.isError, data });
    }
    send(res, 404, { error: 'Not found' });
  } catch (e) {
    send(res, 502, { error: e.message });
  }
}).listen(PORT, '127.0.0.1', () => {
  console.log(`Connected Home console: http://localhost:${PORT}`);
  connect().catch((e) => console.error('[console] could not reach the MCP server:', e.message));
});

process.on('SIGINT', () => { child?.kill(); process.exit(0); });
process.on('SIGTERM', () => { child?.kill(); process.exit(0); });
