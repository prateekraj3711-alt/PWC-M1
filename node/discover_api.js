import fs from 'fs/promises';
import path from 'path';

export function attachApiDiscovery(page, sessionId, outDir) {
  const apiRequests = new Set();
  const apiMap = {
    endpoints: [],
    exportEndpoints: {},
    candidateEndpoints: {},
    documentEndpoints: {},
    timestamp: new Date().toISOString(),
    session_id: sessionId
  };

  page.on('request', (request) => {
    try {
      const url = request.url();
      if (!url) return;
      if (url.includes('/api/') || url.includes('/Api/') || url.includes('/API/')) {
        const u = new URL(url);
        const info = {
          url,
          method: request.method(),
          path: u.pathname,
          query: u.search
        };
        apiRequests.add(JSON.stringify(info));
        const lower = info.path.toLowerCase();
        if (lower.includes('export') || lower.includes('download') || lower.includes('excel')) {
          const tabMatch = lower.match(/(todays|notstarted|draft|rejected|submitted|workinprogress|bgvclosed|inprogress)/i);
          if (tabMatch) {
            apiMap.exportEndpoints[tabMatch[0]] = info;
          } else {
            apiMap.exportEndpoints[info.path] = info;
          }
        } else if (lower.includes('candidate') || lower.includes('candidat')) {
          apiMap.candidateEndpoints[info.path] = info;
        } else if (lower.includes('document') || lower.includes('doc')) {
          apiMap.documentEndpoints[info.path] = info;
        }
      }
    } catch {}
  });

  return async function saveApiMap() {
    apiMap.endpoints = Array.from(apiRequests).map((s) => JSON.parse(s));
    const apiMapPath = path.join(outDir, `pwc_api_map_${sessionId}.json`);
    await fs.writeFile(apiMapPath, JSON.stringify(apiMap, null, 2), 'utf-8');
    return apiMapPath;
  };
}

