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
  const base = path.resolve(__dirname, '../src', name);
  const filename = fs.existsSync(base + '.ts') ? base + '.ts' : base + '.tsx';
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  cache.set(name, module.exports);
  vm.runInThisContext('(function(require,module,exports){' + output + String.fromCharCode(10) + '})', { filename })(request => request.startsWith('.') ? load(path.posix.normalize(path.posix.join(path.posix.dirname(name), request))) : require(request), module, module.exports);
  return module.exports;
}
const settings = load('settings');
const state = load('attendanceState');
const native = load('nativePaths');
const workbook = load('workbookState');
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
test('独立扣减无需补卡，零有效且原因选填', () => {
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480, leaveReason: '公司统一调休' }), '');
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 0, leaveReason: '无需扣减' }), '');
  assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480 }), '');
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
test('处理状态来自后端任务，不将已解决的源缺卡重新放回待处理', () => {
  const day = { covered: true, status: '全天缺卡', requiresReview: true, requiredStandardMinutes: 480, records: [], needsAttention: false, resolutionStatus: 'resolved', attentionReasons: [] };
  assert.equal(state.needsAttention(day), false);
  assert.equal(state.attentionLabel(day), '已处理');
  assert.equal(state.needsAttention({ ...day, status: '来源冲突', needsAttention: true, resolutionStatus: 'pending' }), true);
  assert.equal(state.attentionLabel({ ...day, needsAttention: true, resolutionStatus: 'pending', attentionReasons: [{ code: 'sourceConflict', message: '来源存在冲突', action: '选择来源' }] }), '来源存在冲突');
});
test('手动原因空白省略和null均可保存，非文本仍拒绝', () => {
  for (const reason of [undefined, null, '', '  ']) assert.equal(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480, leaveReason: reason }), '');
  assert.match(state.validateOverride({ workDate: '2026-11-02', compensatoryLeaveMinutes: 480, leaveReason: 5 }), /说明/);
});
/** 生成仅含可携带计算规则与匿名身份的配置检测结果。 */
function portableFile(path, overrides = [], calculation = settings.defaultSettings().calculation) {
  return { inputPath: path, path, employee: { name: '测试员工', account: 'test' }, status: 'valid', message: '', state: { schemaVersion: 1, savedAt: '2026-11-08', settings: calculation, overrides } };
}
test('多工作簿同配置合并修订，同日期一致只保留一次', () => {
  const first = portableFile('一月.xlsx', [{ workDate: '2026-11-02', compensatoryLeaveMinutes: 480 }]);
  const second = portableFile('二月.xlsx', [{ workDate: '2026-11-02', compensatoryLeaveMinutes: 480 }, { workDate: '2026-11-03', compensatoryLeaveMinutes: 0 }]);
  const merged = workbook.combineWorkbookStates([first, second]);
  assert.equal(merged.conflict, ''); assert.equal(merged.state.overrides.length, 2);
  assert.equal(first.state.overrides.length, 1);
});
test('Excel设置或同日修订矛盾不得静默取最后一份', () => {
  const first = portableFile('甲.xlsx', [{ workDate: '2026-11-02', compensatoryLeaveMinutes: 480 }]);
  const second = portableFile('乙.xlsx', [{ workDate: '2026-11-02', compensatoryLeaveMinutes: 0 }]);
  assert.equal(workbook.combineWorkbookStates([first, second]).state, null);
  assert.match(workbook.combineWorkbookStates([first, second]).conflict, /修订/);
  assert.match(workbook.combineWorkbookStates([first, portableFile('丙.xlsx', [], { ...settings.defaultSettings().calculation, standardMinutes: 420 })]).conflict, /系统配置/);
});
test('损坏版本及非法扣减不能作为Excel配置选项', () => {
  const invalid = portableFile('坏.xlsx', [{ workDate: '2026-11-02', compensatoryLeaveMinutes: -480 }]);
  const version = portableFile('未来.xlsx'); version.state.schemaVersion = 2;
  assert.deepEqual(workbook.usableWorkbookFiles([invalid, version]), []);
  assert.equal(workbook.combineWorkbookStates([invalid, version]).state, null);
});
test('工作簿携带配置不包含本机输出目录和派生引擎字段', () => {
  const plain = settings.defaultSettings().calculation;
  assert.deepEqual(workbook.portableSettings({ ...plain, outputRoot: 'C:/个人目录', dayBoundary: '04:00' }), plain);
});
test('规范化路径保留导入别名，移除其他文件不误丢当前覆盖日期', () => {
  const source = { id: 'same-content', path: '/private/tmp/考勤.xlsx', inputPath: '/tmp/考勤.xlsx', name: '考勤.xlsx', startDate: '2026-11-01', endDate: '2026-11-08' };
  const alias = { ...source, inputPath: '/private/tmp/考勤.xlsx', duplicateOf: source.id };
  assert.equal(native.matchesSourceInput(source, '/tmp/考勤.xlsx'), true);
  assert.equal(native.matchesSourceInput(source, '/private/tmp/考勤.xlsx'), false);
  assert.deepEqual([source, alias].filter(item => native.matchesSourceInput(item, '/tmp/考勤.xlsx')), [source]);
  assert.equal(native.matchesSourceInput({ ...source, inputPath: undefined }, source.path), true);
});
test('同名来源选择显示不同完整路径', () => {
  const first = { name: '考勤.xlsx', path: '/归档一/考勤.xlsx' };
  const second = { name: '考勤.xlsx', path: '/归档二/考勤.xlsx' };
  assert.notEqual(native.sourceChoiceLabel(first), native.sourceChoiceLabel(second));
  assert.ok(native.sourceChoiceLabel(first).includes('/归档一/考勤.xlsx'));
});
test('移除文件前识别未解析覆盖，已知路径别名和坏文件移除不误阻止', () => {
  const known = { path: '/private/tmp/旧报表.xlsx', inputPath: '/tmp/旧报表.xlsx' };
  assert.deepEqual(native.unknownCoveragePaths(['/tmp/新报表.xlsx'], [known]), ['/tmp/新报表.xlsx']);
  assert.deepEqual(native.unknownCoveragePaths(['/tmp/旧报表.xlsx'], [known]), []);
  assert.deepEqual(native.unknownCoveragePaths([], [known]), []);
});
test('恢复原始异常时间不重新提交倒置时间，保留手动扣减与单日作息', () => {
  const original = { workDate: '2026-11-02', firstCheckIn: '2026-11-02T10:00', lastCheckOut: '2026-11-02T09:00', compensatoryLeaveMinutes: 480, leaveReason: '公司调休', schedule: { workdayStart: '09:00', workdayEnd: '18:00', lunchStart: '12:00', lunchEnd: '13:00' } };
  assert.match(state.validateOverride(original), /晚于/);
  const restored = state.restoreOriginalTimes(original);
  assert.equal(restored.resetTimes, true);
  assert.equal('firstCheckIn' in restored, false);
  assert.equal('lastCheckOut' in restored, false);
  assert.equal(restored.compensatoryLeaveMinutes, 480);
  assert.equal(restored.leaveReason, '公司调休');
  assert.deepEqual(restored.schedule, original.schedule);
  assert.equal(state.validateOverride(restored), '');
  assert.equal(state.validateOverride({ ...restored, leaveReason: '' }), '');
});
test('人工扣减按最终值拆分，手动零值保留，未知量不冒充零', () => {
  const days = [{ covered: true, isLeaveEdited: true, compensatoryLeaveMinutes: 480 }, { covered: true, isLeaveEdited: true, compensatoryLeaveMinutes: 0 }, { covered: true, isLeaveEdited: false, compensatoryLeaveMinutes: 60 }, { covered: false, isLeaveEdited: false, compensatoryLeaveMinutes: null }];
  const value = state.leaveBreakdown(days);
  assert.equal(value.manualDays.length, 2);
  assert.equal(value.manualMinutes, 480);
  assert.equal(value.automaticMinutes, 60);
  assert.equal(state.leaveBreakdown([...days, { covered: true, isLeaveEdited: false, compensatoryLeaveMinutes: null }]).automaticMinutes, null);
});
test('无原始申请时扣减抽屉仍呈现手动日期、原因和零扣减', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const Drawer = load('components/InsightDrawer').default;
  const days = [480, 480, 0].map((minutes, index) => ({ workDate: '2026-11-0' + (index + 2), covered: true, isLeaveEdited: true, compensatoryLeaveMinutes: minutes, autoCompensatoryLeaveMinutes: 0, leaveReason: index === 2 ? '无需扣减' : '公司统一调休', requiredStandardMinutes: 480, actualEffectiveMinutes: null, extraMinutes: null, netExtraMinutes: -minutes, requiresReview: false, status: '全天缺卡', records: [], dayType: '普通工作日' }));
  const result = { days, summary: { requiredStandardMinutes: 1440, actualEffectiveMinutes: 0, extraMinutes: 0, compensatoryLeaveMinutes: 960, netExtraMinutes: -960 }, calendar: { years: [], warnings: [] }, compensatoryLeaves: [] };
  const html = renderToStaticMarkup(React.createElement(Drawer, { active: 'leave', result, onClose() {}, onSelectDay() {}, onShowAbnormal() {} }));
  assert.ok(html.includes('16小时00分钟'));
  assert.ok(html.includes('公司统一调休'));
  assert.ok(html.includes('2026-11-04'));
  assert.ok(html.includes('无需扣减'));
});
test('净剩抽屉独立展示工时不足，并保留负余额和每日变化', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const Drawer = load('components/InsightDrawer').default;
  const day = { workDate: '2026-10-26', covered: true, requiredStandardMinutes: 480, actualEffectiveMinutes: 450, rawDurationMinutes: 510, lunchDeductionMinutes: 60, extraMinutes: 0, compensatoryLeaveMinutes: 0, shortfallMinutes: 30, netExtraMinutes: -30, needsAttention: false, dayType: '普通工作日' };
  const result = { days: [day], summary: { requiredStandardMinutes: 480, actualEffectiveMinutes: 450, extraMinutes: 0, compensatoryLeaveMinutes: 0, shortfallMinutes: 30, netExtraMinutes: -30 }, calendar: { years: [], warnings: [] }, compensatoryLeaves: [] };
  const html = renderToStaticMarkup(React.createElement(Drawer, { active: 'net', result, onClose() {}, onSelectDay() {}, onShowAbnormal() {} }));
  assert.ok(html.includes('<span>调休扣减</span><strong>0小时00分钟</strong>'));
  assert.ok(html.includes('<span>工时不足</span><strong>0小时30分钟</strong>'));
  assert.ok(html.includes('<span>净剩</span><strong>-0小时30分钟</strong>'));
  assert.ok(html.includes('减少 0小时30分钟 · 余额 -0小时30分钟'));
});
