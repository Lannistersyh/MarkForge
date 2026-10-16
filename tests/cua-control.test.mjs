import test from 'node:test';
import assert from 'node:assert/strict';
import {createController} from '../scripts/cua-control.mjs';

function fixture() {
  const log = [];
  const target = {
    getAXState: async () => {log.push('ax'); return 'result: 42';},
    getAXStateAndScreenshot: async () => {log.push('visual'); return {state: 'calculator', screenshot: new Uint8Array()};},
    click: async t => {log.push(['click', t]);},
    pressKey: async k => {log.push(['key', k]);},
  };
  return {target, log, control: createController(target)};
}

test('four clicks execute between one visual observation and one AX verification', async () => {
  const {control, log} = fixture();
  const observation = await control.observe({visual: true});
  const result = await control.act([1,2,3,4].map(x => ({type:'click', target:[x,1]})),
                                  {frame_id:observation.frame_id, stableLayout:true});
  assert.equal(result.status, 'OK');
  assert.equal(result.completed, 4);
  assert.deepEqual(log.map(x => Array.isArray(x) ? x[0] : x), ['visual','click','click','click','click','ax']);
  assert.match(result.observation.state, /42/);
});

test('rejects an invalid later action before any input', async () => {
  const {control, log} = fixture();
  const {frame_id} = await control.observe();
  const result = await control.act([{type:'click',target:3},{type:'unknown'}], {frame_id});
  assert.equal(result.status, 'REFUSED');
  assert.deepEqual(log, ['ax']);
});

test('partial failure is reported and cannot be automatically retried on an old frame', async () => {
  const {control, target, log} = fixture();
  const {frame_id} = await control.observe();
  target.click = async () => {if (log.length > 1) throw new Error('window closed'); log.push('clicked');};
  const actions = [{type:'click',target:1},{type:'click',target:2}];
  const partial = await control.act(actions, {frame_id});
  assert.equal(partial.status, 'PARTIAL');
  assert.equal(partial.completed, 1);
  const retry = await control.act(actions, {frame_id});
  assert.equal(retry.status, 'REFUSED');
  assert.equal(log.length, 2);
});

test('semantic observation cannot be used for screenshot coordinates', async () => {
  const {control, log} = fixture();
  const {frame_id} = await control.observe();
  const result = await control.act([{type:'click',target:[1,2]}], {frame_id});
  assert.equal(result.status, 'REFUSED');
  assert.deepEqual(log, ['ax']);
});
