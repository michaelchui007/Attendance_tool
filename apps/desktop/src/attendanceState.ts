import type { AttendanceDay, DayOverride } from "./types";
import { validateSchedule } from './settings';

/** 返回适合表格的分钟文本；未知扣减明确展示为待核对。 */
export function compactMinutes(value: number | null, unknown = "未计算"): string {
  if (value === null) return unknown;
  const absolute = Math.abs(value);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  return (value < 0 ? "−" : "") + hours + "h" + (minutes ? " " + minutes + "m" : "");
}

/** 按日期字符串处理考勤时间，避免电脑时区改变报表归属。 */
export function workDateFor(timestamp: string): string {
  const [day, time] = timestamp.split("T");
  if (time >= "04:00") return day;
  const date = new Date(day + "T12:00:00Z");
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

/** 校验单日覆盖值。
 *
 * Args:
 *   value: 当前日期的上、下班覆盖值。
 * Returns:
 *   错误说明；通过校验时返回空字符串。
 */
export function validateOverride(value: DayOverride): string {
  if (value.compensatoryLeaveMinutes !== undefined && value.compensatoryLeaveMinutes !== null) {
    if (!Number.isInteger(value.compensatoryLeaveMinutes) || value.compensatoryLeaveMinutes < 0 || value.compensatoryLeaveMinutes > 1440) return '调休扣减须为 0～24 小时，并精确到分钟。';
    if (value.leaveReason != null && typeof value.leaveReason !== 'string') return '手动扣减说明格式无效。';
  }
  if (value.schedule) { const problem = validateSchedule(value.schedule); if (problem) return problem; }
  if (value.resetTimes === true) return '';
  for (const timestamp of [value.firstCheckIn, value.lastCheckOut]) {
    if (!timestamp) continue;
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(timestamp)) return "请输入完整的日期和时间。";
    const parsed = new Date(timestamp + ":00Z");
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 16) !== timestamp) return "日期或时间无效。";
    if (workDateFor(timestamp) !== value.workDate) return "时间须在当天 04:00 至次日 04:00 前；请到对应日期修订。";
  }
  if (value.firstCheckIn && value.lastCheckOut && value.lastCheckOut <= value.firstCheckIn) return "下班时间必须晚于上班时间。";
  return "";
}

/** 恢复时间使用显式撤销标记，不把可能无效的原始时间伪装成新输入。 */
export function restoreOriginalTimes(value: DayOverride): DayOverride {
  const { firstCheckIn: _first, lastCheckOut: _last, ...remaining } = value;
  return { ...remaining, resetTimes: true };
}

/** 按最终实际扣减拆分自动与人工构成，手动零值也保留明细。 */
export function leaveBreakdown(days: AttendanceDay[]) {
  const covered = days.filter(day => day.covered !== false);
  const manualDays = covered.filter(day => day.isLeaveEdited);
  const automaticDays = covered.filter(day => !day.isLeaveEdited);
  const total = (items: AttendanceDay[]): number | null => items.some(day => day.compensatoryLeaveMinutes === null) ? null : items.reduce((sum, day) => sum + (day.compensatoryLeaveMinutes || 0), 0);
  return { manualDays, manualMinutes: total(manualDays), automaticMinutes: total(automaticDays) };
}

/** 仅将需要用户处理的日期列为待处理，休息日无记录不制造告警。 */
export function needsAttention(day: AttendanceDay): boolean {
  if (day.covered === false) return false;
  if (typeof day.needsAttention === 'boolean') return day.needsAttention;
  return day.requiresReview || (day.status !== "正常" && (day.requiredStandardMinutes > 0 || day.records.length > 0 || Boolean(day.firstCheckIn || day.lastCheckOut)));
}

/** 待处理标签描述当前任务；原始缺卡状态单独保留在详情中。 */
export function attentionLabel(day: AttendanceDay): string {
  if (day.covered === false) return '未导入';
  if (day.resolutionStatus === 'resolved') return '已处理';
  if (needsAttention(day)) return day.attentionReasons?.map(reason => reason.message).join('；') || (day.requiresReview ? '调休信息待核对' : day.status);
  return day.requiredStandardMinutes === 0 ? '休息日' : '正常';
}
