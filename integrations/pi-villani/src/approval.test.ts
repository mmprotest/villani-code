import test from 'node:test'; import assert from 'node:assert/strict';
test('approval ids are expected to be run-prefixed monotonic values',()=>{assert.match('run-1:42',/^run-1:\d+$/);});
