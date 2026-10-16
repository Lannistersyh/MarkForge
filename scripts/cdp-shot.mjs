#!/usr/bin/env node
/** 原生 CDP 截图：直连调试端口，开 tab → 截图 → 关 tab。
 *  用法: node cdp-shot.mjs <port> <url> <out.png>
 *  不依赖任何 npm 包（Node 22+ 原生 WebSocket）。 */
const [port, url, out] = process.argv.slice(2);
if (!port || !url || !out) {
  console.error('usage: node cdp-shot.mjs <port> <url> <out.png>');
  process.exit(2);
}
const BASE = `http://127.0.0.1:${port}`;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  // 1) 新建 tab（PUT /json/new，新版 Chromium 需要 PUT）
  let res = await fetch(`${BASE}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  if (!res.ok) {  // 老版本回退 GET
    res = await fetch(`${BASE}/json/new?${encodeURIComponent(url)}`);
  }
  const tab = await res.json();
  const { id: targetId, webSocketDebuggerUrl: wsUrl } = tab;
  console.error(`tab: ${targetId}`);

  await sleep(1500); // 等加载

  // 2) WS 连 tab，Page.captureScreenshot
  const ws = new WebSocket(wsUrl);
  let msgId = 0;
  const pending = new Map();
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`CDP ${method} 超时`));
      }
    }, 15000);
  });
  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
    }
  };
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });

  // 3) 设置视口 + 截图
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(300);
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  const { writeFileSync } = await import('node:fs');
  writeFileSync(out, Buffer.from(data, 'base64'));
  console.error(`saved: ${out}`);

  // 4) 关 tab
  ws.close();
  await fetch(`${BASE}/json/close/${targetId}`).catch(() => {});
}

main().catch(e => { console.error(e.message); process.exit(1); });
