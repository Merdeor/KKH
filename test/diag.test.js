const test = require('node:test');
const assert = require('node:assert/strict');
const D = require('../diag.js');

test('gradeBattery', () => {
  assert.equal(D.gradeBattery(null, false).status, 'na');
  assert.equal(D.gradeBattery(0.05, false).status, 'bad');
  assert.equal(D.gradeBattery(0.05, true).status, 'ok');
  assert.equal(D.gradeBattery(0.15, false).status, 'warn');
  assert.equal(D.gradeBattery(0.8, false).status, 'ok');
  assert.match(D.gradeBattery(0.8, true).msg, /80%.*충전 중/);
});

test('gradeStorage', () => {
  assert.equal(D.gradeStorage(undefined, 100).status, 'na');
  assert.equal(D.gradeStorage(95, 100).status, 'bad');
  assert.equal(D.gradeStorage(80, 100).status, 'warn');
  assert.equal(D.gradeStorage(10, 100).status, 'ok');
});

test('gradeMemory', () => {
  assert.equal(D.gradeMemory(undefined).status, 'na');
  assert.equal(D.gradeMemory(1).status, 'bad');
  assert.equal(D.gradeMemory(2).status, 'warn');
  assert.equal(D.gradeMemory(4).status, 'ok');
});

test('gradeNetwork', () => {
  assert.equal(D.gradeNetwork(false).status, 'bad');
  assert.equal(D.gradeNetwork(true, undefined).status, 'ok');
  assert.equal(D.gradeNetwork(true, '2g', 0.1, 900).status, 'bad');
  assert.equal(D.gradeNetwork(true, '3g', 1.0, 300).status, 'warn');
  assert.equal(D.gradeNetwork(true, '4g', 0.9, 50).status, 'warn');
  assert.equal(D.gradeNetwork(true, '4g', 10, 50).status, 'ok');
});

test('gradeCpu / gradeFps', () => {
  assert.equal(D.gradeCpu(NaN).status, 'na');
  assert.equal(D.gradeCpu(100).status, 'ok');
  assert.equal(D.gradeCpu(300).status, 'warn');
  assert.equal(D.gradeCpu(700).status, 'bad');
  assert.equal(D.gradeFps(undefined).status, 'na');
  assert.equal(D.gradeFps(60).status, 'ok');
  assert.equal(D.gradeFps(40).status, 'warn');
  assert.equal(D.gradeFps(20).status, 'bad');
});

test('gradeTouch', () => {
  assert.equal(D.gradeTouch(0, 0).status, 'na');
  assert.equal(D.gradeTouch(60, 60, 5).status, 'ok');
  assert.equal(D.gradeTouch(59, 60).status, 'warn');
  assert.equal(D.gradeTouch(40, 60).status, 'bad');
  assert.match(D.gradeTouch(40, 60).msg, /20칸 무반응/);
});

test('gradeMic / gradeSensor / yesNo', () => {
  assert.equal(D.gradeMic(0).status, 'bad');
  assert.equal(D.gradeMic(0.2).status, 'ok');
  assert.equal(D.gradeSensor(0, false).status, 'bad');
  assert.equal(D.gradeSensor(5, false).status, 'warn');
  assert.equal(D.gradeSensor(5, true).status, 'ok');
  assert.equal(D.yesNo(true, 'a', 'b').msg, 'a');
  assert.equal(D.yesNo(false, 'a', 'b').status, 'bad');
});

test('summarize', () => {
  assert.equal(D.summarize([]).score, null);
  assert.equal(D.summarize(['pending', 'na']).verdict, '아직 검사 전입니다');
  const s = D.summarize(['ok', 'warn', 'bad', 'na', 'pending']);
  assert.equal(s.verdict, '문제가 발견되었습니다');
  assert.equal(s.score, 50);
  assert.equal(D.summarize(['ok', 'warn']).verdict, '일부 항목에 주의가 필요합니다');
  assert.equal(D.summarize(['ok', 'ok']).score, 100);
});

test('fmtBytes', () => {
  assert.equal(D.fmtBytes(512), '512B');
  assert.equal(D.fmtBytes(1536), '1.5KB');
  assert.equal(D.fmtBytes(5 * 1024 ** 3), '5.0GB');
});
