import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 3000;

const MIME = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.js': 'application/javascript',
  '.mjs': 'application/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

// Proxy UEFA API calls to avoid CORS
async function proxyFetch(targetUrl, res, maxAge = 60) {
  try {
    const upstream = await fetch(targetUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    const body = await upstream.text();
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': `public, max-age=${maxAge}`,
    });
    res.end(body);
  } catch(e) {
    res.writeHead(502);
    res.end('Proxy error: ' + e.message);
  }
}

http.createServer((req, res) => {
  const rawUrl = decodeURIComponent(req.url);
  const url = rawUrl.split('?')[0];

  // CORS preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*' });
    res.end();
    return;
  }

  // Proxy: UCL fixtures list
  if (url === '/api/ucl-fixtures') {
    proxyFetch('https://gaming.uefa.com/en/uclfantasy/services/feeds/fixtures/fixtures_80_en.json', res, 300);
    return;
  }

  // Proxy: individual match detail  /api/ucl-match/2048048
  const matchProxy = url.match(/^\/api\/ucl-match\/(\d+)$/);
  if (matchProxy) {
    proxyFetch(`https://match.uefa.com/v5/matches/${matchProxy[1]}`, res, 60);
    return;
  }

  // Serve cached API data for local dev
  if (url === '/api/data') {
    const qs = new URLSearchParams(rawUrl.split('?')[1] || '');
    const md = parseInt(qs.get('md')) || 12;
    const cacheFile = path.join(__dirname, 'cache', `md${String(md).padStart(2,'0')}.json`);
    fs.readFile(cacheFile, (err, data) => {
      if (err) { res.writeHead(404, {'Content-Type':'application/json'}); res.end('{"error":"no cache"}'); return; }
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      res.end(data);
    });
    return;
  }

  // Static file serving
  let filePath = path.join(__dirname, url === '/' ? '/index.html' : url);
  const ext = path.extname(filePath).toLowerCase();
  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(PORT, () => console.log(`Serving on http://localhost:${PORT}`));
