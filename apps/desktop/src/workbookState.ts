import type { CalculationSettings, DayOverride, WorkbookState, WorkbookStateFile } from './types';
import { validateSettings } from './settings';
import { validateOverride } from './attendanceState';

/** 只投影允许跨电脑携带的计算字段，忽略引擎快照的派生值。 */
export function portableSettings(value: CalculationSettings): CalculationSettings {
  const schedule = (item: CalculationSettings) => ({ workdayStart: item.workdayStart, workdayEnd: item.workdayEnd, lunchStart: item.lunchStart, lunchEnd: item.lunchEnd });
  return { standardMinutes: value.standardMinutes, ...schedule(value), dateRules: value.dateRules.map(rule => ({ id: rule.id, name: rule.name, startDate: rule.startDate, endDate: rule.endDate, ...schedule({ ...value, ...rule }) })) };
}

/** 稳定序列化用于比较数据，不依赖Excel保存JSON时的键顺序。 */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}';
  return JSON.stringify(value);
}

/** 检测结果仍经过前端格式校验；无效状态不能作为可选配置。 */
export function usableWorkbookFiles(files: WorkbookStateFile[]): WorkbookStateFile[] {
  return files.filter(file => {
    try { return file.status === 'valid' && file.state?.schemaVersion === 1 && !validateSettings(file.state.settings) && Array.isArray(file.state.overrides) && file.state.overrides.every(item => !validateOverride(item)); } catch { return false; }
  });
}

/** 配置一致且同日修订不矛盾时才合并；否则由用户选具体文件方案。 */
export function combineWorkbookStates(files: WorkbookStateFile[]): { state: WorkbookState | null; conflict: string } {
  const valid = usableWorkbookFiles(files);
  if (!valid.length) return { state: null, conflict: '' };
  const first = valid[0].state!;
  const employee = canonical(valid[0].employee);
  const config = canonical(portableSettings(first.settings));
  const overrides: Record<string, DayOverride> = {};
  for (const file of valid) {
    if (canonical(file.employee) !== employee) return { state: null, conflict: '工作簿中的员工身份不一致，不能合并应用。' };
    if (canonical(portableSettings(file.state!.settings)) !== config) return { state: null, conflict: '多个工作簿采用不同的系统配置，请明确选择一份方案。' };
    for (const item of file.state!.overrides) {
      if (overrides[item.workDate] && canonical(overrides[item.workDate]) !== canonical(item)) return { state: null, conflict: item.workDate + ' 的修订在多个工作簿中不一致，请选择一份方案。' };
      overrides[item.workDate] = item;
    }
  }
  return { state: { ...first, settings: portableSettings(first.settings), overrides: Object.values(overrides).sort((a, b) => a.workDate.localeCompare(b.workDate)) }, conflict: '' };
}
