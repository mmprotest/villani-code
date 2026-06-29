import test from 'node:test'; import assert from 'node:assert/strict';
import { assetName, executableRelativePath, platformKey, VILLANI_RUNTIME_VERSION } from './runtimeConfig.js';
import { parseChecksums } from './runtime.js';
import { sanitizedEnv } from './process.js';
import { zeroUsage } from './modelProxy.js';
import { visibleChangedFiles } from './render.js';
test('runtime asset helpers',()=>{assert.equal(assetName(platformKey('linux','x64')),`villani-runtime-v${VILLANI_RUNTIME_VERSION}-linux-x64.tar.gz`); assert.equal(executableRelativePath('win32-x64'),'villani-code/villani-code.exe');});
test('checksum parsing and env sanitization',()=>{assert.equal(parseChecksums('abc  file.tgz').get('file.tgz'),'abc'); const e=sanitizedEnv({proxyMode:true,env:{OPENAI_API_KEY:'x',PATH:'p'}}); assert.equal(e.OPENAI_API_KEY,undefined); assert.equal(e.PATH,'p');});
test('usage and render filters',()=>{assert.deepEqual(zeroUsage(),{input:0,output:0,totalTokens:0}); assert.deepEqual(visibleChangedFiles(['a.py','.villani/x','x.pyc']),['a.py']);});
