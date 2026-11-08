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
    if (!value.leaveReason?.trim()) return '请填写手动扣减原因。';
  }
  if (value.schedule) { const problem = validateSchedule(value.schedule); if (problem) return problem; }
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

/** 将原始时间转换为分钟输入值，恢复操作不修改原始记录。 */
export function originalOverride(day: AttendanceDay): DayOverride {
  return { workDate: day.workDate, firstCheckIn: day.originalFirstCheckIn?.slice(0, 16) || null, lastCheckOut: day.originalLastCheckOut?.slice(0, 16) || null };
}

/** 仅将需要用户处理的日期列为待处理，休息日无记录不制造告警。 */
export function needsAttention(day: AttendanceDay): boolean {
  if (day.covered === false) return false;
  return day.requiresReview || (day.status !== "正常" && (day.requiredStandardMinutes > 0 || day.records.length > 0 || Boolean(day.firstCheckIn || day.lastCheckOut)));
}
