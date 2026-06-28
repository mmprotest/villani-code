import test from 'node:test'; import assert from 'node:assert/strict'; import { EventEmitter } from 'node:events';
import { VillaniBridgeProcess } from './process.js';
test('bridge waitForEvent resolves matching event',async()=>{const b=Object.assign(new EventEmitter(),{off:EventEmitter.prototype.off}) as any as VillaniBridgeProcess; Object.setPrototypeOf(b,VillaniBridgeProcess.prototype); const p=b.waitForEvent('run_aborted',50); b.emit('event',{type:'run_aborted'}); assert.equal((await p)?.type,'run_aborted');});
