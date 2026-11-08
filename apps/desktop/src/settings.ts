import type { AppSettings, CalculationSettings, Schedule } from './types';

export const SETTINGS_KEY = 'attendance.settings.v1';

/** 返回新配置对象，避免恢复默认时共享可变日期规则。 */
export function defaultSettings(): AppSettings {
  return { version: 1, outputRoot: '', calculation: { standardMinutes: 480, workdayStart: '09:00', workdayEnd: '18:30', lunchStart: '12:00', lunchEnd: '13:30', dateRules: [] } };
}

/** 将严格 HH:mm 转为整数分钟，非法输入保持 NaN。 */
export function timeMinutes(value: string): number {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) return NaN;
  return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
}

/** 作息净长只用于解释，不回写标准工时。 */
export function scheduleMinutes(value: Schedule): number {
  return timeMinutes(value.workdayEnd) - timeMinutes(value.workdayStart) - (value.lunchStart !== null && value.lunchEnd !== null ? timeMinutes(value.lunchEnd) - timeMinutes(value.lunchStart) : 0);
}

/** 校验同日白班和可选单段午休。 */
export function validateSchedule(value: Schedule): string {
  const start = timeMinutes(value.workdayStart), end = timeMinutes(value.workdayEnd);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return '正常下班时间必须晚于上班时间，且为有效的同日时间。';
  if (value.lunchStart === null && value.lunchEnd === null) return '';
  if (value.lunchStart === null || value.lunchEnd === null) return '请完整填写午休起止时间，或选择无午休。';
  const lunchStart = timeMinutes(value.lunchStart), lunchEnd = timeMinutes(value.lunchEnd);
  if (!Number.isFinite(lunchStart) || !Number.isFinite(lunchEnd) || lunchStart < start || lunchEnd > end || lunchEnd <= lunchStart || scheduleMinutes(value) <= 0) return '午休必须位于正常上下班范围内，结束晚于开始，净工作时长须大于零。';
  return '';
}

/** 日期必须确实存在，不能让 JavaScript 自动进位掩盖错误。 */
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + 'T12:00:00Z');
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

/** 验证标准值、日期段和重叠，返回可直接展示的具体错误。 */
export function validateSettings(value: CalculationSettings): string {
  if (!Number.isInteger(value.standardMinutes) || value.standardMinutes <= 0 || value.standardMinutes > 1440) return '标准工时须大于 0 且不超过 24 小时，精确到分钟。';
  const problem = validateSchedule(value);
  if (problem) return problem;
  if (!Array.isArray(value.dateRules)) return '日期规则格式无效。';
  const sorted = [...value.dateRules].sort((a, b) => a.startDate.localeCompare(b.startDate));
  for (let i = 0; i < sorted.length; i++) {
    const rule = sorted[i];
    if (!rule.id || !validDate(rule.startDate) || !validDate(rule.endDate) || rule.startDate > rule.endDate) return '日期段须填写有效起止日期，结束不能早于开始。';
    if (validateSchedule(rule)) return (rule.name || rule.startDate) + '：' + validateSchedule(rule);
    if (i && rule.startDate <= sorted[i - 1].endDate) return '日期段重叠：' + sorted[i - 1].startDate + '～' + sorted[i - 1].endDate + ' 与 ' + rule.startDate + '～' + rule.endDate;
    if (sorted.some((other, index) => index !== i && other.id === rule.id)) return '日期规则标识重复。';
  }
  return '';
}

/** 仅恢复经过验证的版本化本地配置，损坏时给出明确提示。 */
export function readSettings(storage: Pick<Storage, 'getItem'>): { settings: AppSettings; error: string } {
  try {
    const raw = storage.getItem(SETTINGS_KEY);
    if (!raw) return { settings: defaultSettings(), error: '' };
    const value = JSON.parse(raw) as AppSettings;
    if (value.version !== 1 || typeof value.outputRoot !== 'string' || validateSettings(value.calculation)) throw new Error('invalid');
    return { settings: value, error: '' };
  } catch { return { settings: defaultSettings(), error: '保存的系统配置无法读取，已暂用默认值；请检查并重新保存。' }; }
}

/** 保存失败直接抛出，由调用方保留草稿与上一有效配置。 */
export function saveSettings(storage: Pick<Storage, 'setItem'>, value: AppSettings): void {
  const problem = validateSettings(value.calculation);
  if (problem) throw new Error(problem);
  storage.setItem(SETTINGS_KEY, JSON.stringify(value));
}

/** 把小时输入转换为分钟，不静默截断超过分钟精度的数值。 */
export function hoursToMinutes(value: string, allowNegative = false): number {
  if (!value.trim()) return NaN;
  const hours = Number(value);
  if (!Number.isFinite(hours) || (!allowNegative && hours < 0)) return NaN;
  const minutes = Math.abs(hours) * 60;
  return Math.abs(minutes - Math.round(minutes)) < 1e-7 ? Math.round(minutes) : NaN;
}
