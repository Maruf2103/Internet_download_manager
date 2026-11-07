"use strict";
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('idm/chrome_extension/background.js', 'utf8');
async function run({capture, fail, url='https://example.org/setup.exe'}) {
  const order = []; let handler;
  const noop = { addListener() {} };
  const chrome = {
    runtime: { onInstalled: noop, onMessage: noop, sendNativeMessage: async () => { order.push('native'); if (fail) throw Error('Host unavailable'); return {ok:true}; } },
    contextMenus: { onClicked: noop },
    storage: { local: { get: async () => ({capture}), set: async () => {} } },
    action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setTitle: async () => {} },
    downloads: { onDeterminingFilename: {addListener: fn => {handler=fn;}}, cancel: async () => {order.push('cancel');} },
  };
  vm.runInNewContext(source, {chrome, URL});
  handler({id:1,url,filename:'C:\\Downloads\\setup.exe'}, () => {order.push('suggest');});
  await new Promise(resolve => setImmediate(resolve));
  return order;
}
(async () => {
  assert.deepEqual(await run({capture:false}), ['suggest']);
  assert.deepEqual(await run({capture:true}), ['suggest','native','cancel']);
  assert.deepEqual(await run({capture:true,fail:true}), ['suggest','native']);
  assert.deepEqual(await run({capture:true,url:'http://127.0.0.1:8765/api/jobs/a/file'}), ['suggest']);
  console.log('Extension handoff logic passed: opt-in, accept before cancel, native failure preserves Chrome download, and local Flow saves ignored.');
})().catch(error => {console.error(error);process.exitCode=1;});
