/** 使用项目 TypeScript 编译器测试实际前端模块，无需额外测试依赖。 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const cache = new Map();
/** 内存转译被测模块，保留真实模块间调用。 */
function load(name) {
  if (cache.has(name)) return cache.get(name);
  const filename = path.resolve(__dirname, '../src', name + '.ts');
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  cache.set(name, module.exports);
  vm.runInThisContext('(function(require,module,exports){' + output + String.fromCharCode(10) + '})', { filename })(request => request.startsWith('./') ? load(request.slice(2)) : require(request), module, module.exports);
  return module.exports;
}
const settings = load('settings');
const state = load('attendanceState');
const native = load('nativePaths');
test('标准与作息独立', () => {
  const value = settings.defaultSettings(); value.calculation.workdayEnd = '18:00';
  assert.equal(settings.scheduleMinutes(value.calculation), 450);
  assert.equal(settings.validateSettings(value.calculation), '');
  let stored; settings.saveSettings({ setItem: (_key, data) => { stored = data; } }, value);
  assert.equal(JSON.parse(stored).calculation.standardMinutes, 480);
});
test('日期段重叠拒绝，相邻跨月范围有效', () => {
  const value = settings.defaultSettings().calculation;
  value.dateRules = [{ ...value, id: 'a', name: '夏季', startDate: '2026-09-01', endDate: '2026-10-01' }, { ...value, id: 'b', name: '冬季', startDate: '2026-10-01', endDate: '2026-10-31' }];
  assert.match(settings.validateSettings(value), /重叠/);
  value.dateRules[1].startDate = '2026-10-02'; assert.equal(settings.validateSettings(value), '');
});
test('无效日期、零标准及越界午休不能生效', () => {
  const value = settings.defaultSettings().calculation;
  assert.match(settings.validateSettings({ ...value, standardMinutes: 0 }), /标准/);
  assert.match(settings.validateSchedule({ ...value, lunchStart: '08:00' }), /午休/);
  assert.equal(settings.validateSchedule({ ...value, lunchStart: null, lunchEnd: null }), '');
  assert.match(settings.validateSettings({ ...value, dateRules: [{ ...value, id: 'x', name: '', startDate: '2026-02-30', endDate: '2026-03-02' }] }), /日期/);
});
test('扣减负号归一、分钟精度与空值', () => {
  assert.equal(settings.hoursToMinutes('-8', true), 480); assert.equal(settings.hoursToMinutes('0', true), 0);
  assert.ok(Number.isNaN(settings.hoursToMinutes('', true))); assert.ok(Number.isNaN(settings.hoursToMinutes('0.001', true)));
  assert.equal(settings.hoursToMinutes('7.5'), 450);
});
test('独立扣减无需补卡，零有效且原因必填', () => {
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480, leaveReason: '公司统一调休' }), '');
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 0, leaveReason: '无需扣减' }), '');
  assert.match(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480 }), /原因/);
  assert.match(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: NaN, leaveReason: '修改' }), /分钟/);
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: null }), '');
});
test('04:00归属边界', () => {
  assert.equal(state.workDateFor('2026-11-03T03:59'), '2026-11-02'); assert.equal(state.workDateFor('2026-11-03T04:00'), '2026-11-03');
  assert.match(state.validateOverride({ workDate: '2026-11-02', firstCheckIn: '2026-11-02T09:00', lastCheckOut: '2026-11-03T04:00' }), /对应日期/);
});
test('中文及代理对路径数值往返无损', () => {
  const original = 'C:' + String.fromCharCode(92) + '用户/小明/考勤 🗓️/十一月 日报.xlsx';
  assert.equal(native.decodeNativePath({ path: '错误文本', pathUtf16: native.pathUtf16(original) }), original);
});
test('损坏配置回退提示，保存失败不吞异常', () => {
  const restored = settings.readSettings({ getItem: () => '{invalid' }); assert.equal(restored.settings.calculation.standardMinutes, 480); assert.match(restored.error, /无法读取/);
  assert.throws(() => settings.saveSettings({ setItem: () => { throw Error('quota'); } }, settings.defaultSettings()), /quota/);
});
test('未导入日期与已覆盖缺卡分开', () => {
  assert.equal(state.needsAttention({ covered: false, requiresReview: false, status: '未导入' }), false);
  assert.equal(state.needsAttention({ covered: true, requiresReview: false, status: '全天缺卡', requiredStandardMinutes: 480, records: [] }), true);
});
