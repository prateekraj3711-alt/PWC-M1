import 'dotenv/config';
import express from 'express';
import pino from 'pino';
import fetch from 'cross-fetch';
import fs from 'fs/promises';
import path from 'path';
import { loginAndDiscover } from './login.js';

const app = express();
app.use(express.json({ limit: '2mb' }));
const log = pino({ level: process.env.LOG_LEVEL || 'info' });

const PORT = Number(process.env.PORT || 3000);
const EXPORT_SERVICE_URL = process.env.EXPORT_SERVICE_URL || 'http://localhost:8000';
const INTERVAL_MIN = Number(process.env.RUN_INTERVAL_MINUTES || 105);

let runLock = false;

async function rotateSessions() {
  const tmp = '/tmp';
  const files = (await fs.readdir(tmp).catch(() => [])).filter(f => f.endsWith('.json'));
  const sorted = await Promise.all(files.map(async f => ({ f, t: (await fs.stat(path.join(tmp, f))).mtimeMs })));
  sorted.sort((a, b) => b.t - a.t);
  const keep = new Set(sorted.slice(0, 3).map(x => x.f));
  for (const { f } of sorted.slice(3)) {
    await fs.unlink(path.join(tmp, f)).catch(() => {});
  }
  log.info({ kept: Array.from(keep) }, 'Rotated session files');
}

async function doRun(triggerSource = 'scheduler') {
  if (runLock) {
    log.warn('Run already in progress, skipping');
    return { ok: false, message: 'Run in progress' };
  }
  runLock = true;
  try {
    await rotateSessions();
    const { sessionId, sessionPath, apiMapPath, storageState } = await loginAndDiscover();
    log.info({ sessionId, sessionPath, apiMapPath }, 'Login & discovery complete');

    let apiMap = null;
    try {
      const data = await fs.readFile(apiMapPath, 'utf-8');
      apiMap = JSON.parse(data);
    } catch {}

    const body = {
      session_id: sessionId,
      storage_state: storageState,
      api_map: apiMap
    };

    const health = await fetch(`${EXPORT_SERVICE_URL}/health`).catch(() => null);
    if (!health || !health.ok) {
      log.warn('Export service health not OK; proceeding anyway');
    }

    const res = await fetch(`${EXPORT_SERVICE_URL}/trigger-fetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      log.error({ status: res.status, json }, 'Export trigger failed');
      return { ok: false, message: 'Export trigger failed', json };
    }
    log.info({ json }, 'Export trigger success');
    return { ok: true, message: 'Run started', result: json };
  } catch (e) {
    log.error({ err: e.message }, 'Run error');
    return { ok: false, message: e.message };
  } finally {
    runLock = false;
  }
}

app.get('/health', (req, res) => {
  res.json({ ok: true, uptime: process.uptime(), scheduler_interval_min: INTERVAL_MIN });
});

app.post('/login-and-run', async (req, res) => {
  const result = await doRun('manual');
  res.status(result.ok ? 200 : 500).json(result);
});

app.listen(PORT, () => {
  log.info({ PORT }, 'Node scheduler listening');
  doRun('startup').then(() => {}).catch(() => {});
  setInterval(() => doRun('interval'), INTERVAL_MIN * 60 * 1000);
});

