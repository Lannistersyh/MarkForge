#!/usr/bin/env node
/** 原生 CDP 坐标点击：开 tab → 截图 → 纯坐标点击 → 截图 → 关 tab。
 *  用法: node cdp-click.mjs <port> <url> <x> <y> [out-before] [out-after]
 *
 *  关键性质：Input.dispatchMouseEvent 按像素坐标派发鼠标事件，
 *  全程不读 DOM、不用选择器 —— 这就是 L4 视觉操作的本义。
 *  坐标从哪来？由 MarkForge 的 grounding（截图 → 检测 → 查询）算出。
 *  依赖：Node 22+（原生 WebSocket），无 npm 包。
 */
const [port, url, x, y, outBefore, outAfter] = process.argv.slice(2);
if (!port || !url || !x || !y) {
  console.error('usage: node cdp-click.mjs <port> <url> <x> <y> [out-before] [out-after]');
  process.exit(2);
}
const X = parseInt(x, 10), Y = parseInt(y, 10);
const BASE = `http://127.0.0.1:${port}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const { writeFileSync } = await import('node:fs');

  // 1) 新建 tab
  let res = await fetch(`${BASE}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  if (!res.ok) res = await fetch(`${BASE}/json/new?${encodeURIComponent(url)}`);
  const tab = await res.json();
  const { id: targetId, webSocketDebuggerUrl: wsUrl } = tab;
  console.error(`tab: ${targetId}`);

  // 2) WS 连接
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
        reject(new Error(`CDP ${method} timeout`));
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
    ws.onopen = resolve; ws.onerror = reject;
  });

  // 3) 视口 + 加载
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(1500);

  // 4) 点击前截图（证据链）
  if (outBefore) {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(outBefore, Buffer.from(data, 'base64'));
    console.error(`before: ${outBefore}`);
  }

  // 5) 纯坐标点击 —— 不读 DOM，坐标即全部信息
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: X, y: Y, button: 'left', clickCount: 1,
  });
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: X, y: Y, button: 'left', clickCount: 1,
  });
  await sleep(600);

  // 6) 点击后截图（验证页面响应）
  if (outAfter) {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(outAfter, Buffer.from(data, 'base64'));
    console.error(`after: ${outAfter}`);
  }

  // 7) 关 tab
  ws.close();
  await fetch(`${BASE}/json/close/${targetId}`).catch(() => {});
  console.error(`clicked (${X}, ${Y}) — pure coordinates, no DOM read`);
}

main().catch(e => { console.error(e.message); process.exit(1); });
