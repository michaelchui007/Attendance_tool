import { useState } from 'react';
import type { AttendanceDay, DayOverride } from '../types';
import { compactMinutes, originalOverride, validateOverride } from '../attendanceState';
import { hoursToMinutes } from '../settings';
import ScheduleFields from './ScheduleFields';

interface Props { day: AttendanceDay; value: DayOverride; onChange: (value: DayOverride) => void; disabled: boolean; }

/** 每日修订按字段保存，单独扣减不要求补造时间。 */
export default function DayEditor({ day, value, onChange, disabled }: Props) {
  const [hours, setHours] = useState(value.compensatoryLeaveMinutes == null ? '' : String(value.compensatoryLeaveMinutes / 60));
  const manual = value.compensatoryLeaveMinutes !== undefined && value.compensatoryLeaveMinutes !== null;
  const first = value.firstCheckIn === undefined ? day.firstCheckIn : value.firstCheckIn;
  const last = value.lastCheckOut === undefined ? day.lastCheckOut : value.lastCheckOut;
  const error = validateOverride(value);

  /** 修改一项时间时传递整对时间，未编辑时间维持当前有效值。 */
  function changeTime(field: 'firstCheckIn' | 'lastCheckOut', input: string) {
    onChange({ ...value, firstCheckIn: first?.slice(0, 16) || null, lastCheckOut: last?.slice(0, 16) || null, [field]: input || null });
  }

  return <div className="day-editor"><fieldset disabled={disabled} className="time-editor"><legend>打卡时间</legend><label>上班日期与时间<input aria-label="上班日期与时间" type="datetime-local" value={first?.slice(0, 16) || ''} onChange={e => changeTime('firstCheckIn', e.target.value)} /></label><label>下班日期与时间<input aria-label="下班日期与时间" type="datetime-local" value={last?.slice(0, 16) || ''} onChange={e => changeTime('lastCheckOut', e.target.value)} /></label><small>04:00 前归前一天；留空保持缺卡。</small><button className="text-button" onClick={() => onChange({ ...value, ...originalOverride(day) })}>恢复原始打卡时间</button></fieldset>
    <fieldset className="deduction-editor" disabled={disabled}><legend>调休扣减</legend><label>扣减方式<select aria-label="扣减方式" value={manual ? 'manual' : 'auto'} onChange={e => { setHours(e.target.value === 'manual' ? '0' : ''); onChange({ ...value, compensatoryLeaveMinutes: e.target.value === 'manual' ? 0 : null, leaveReason: e.target.value === 'manual' ? value.leaveReason || '' : '' }); }}><option value="auto">自动计算</option><option value="manual">手动指定</option></select></label>
      <p>自动建议：{compactMinutes(day.autoCompensatoryLeaveMinutes, '待核对')}</p>
      {manual && <><label>扣减调休（小时）<input aria-label="扣减调休（小时）" type="number" step="any" value={hours} onChange={e => { setHours(e.target.value); onChange({ ...value, compensatoryLeaveMinutes: hoursToMinutes(e.target.value, true) }); }} onBlur={() => { const minutes = hoursToMinutes(hours, true); if (Number.isFinite(minutes)) setHours(String(minutes / 60)); }} /></label><small>输入 8 或 −8 均表示扣除 8 小时。0 表示明确不扣。</small><label>手动扣减原因<input aria-label="手动扣减原因" value={value.leaveReason || ''} placeholder="例如：公司统一安排调休" onChange={e => onChange({ ...value, leaveReason: e.target.value })} /></label><p className="deduction-preview">对净额的影响：−{Number.isFinite(value.compensatoryLeaveMinutes) ? compactMinutes(value.compensatoryLeaveMinutes!) : '待填写'}</p>{value.compensatoryLeaveMinutes! > day.schedule.standardMinutes && <p className="warning-text">扣减超过标准工时，将按手动值应用。</p>}<button className="text-button" onClick={() => { setHours(''); onChange({ ...value, compensatoryLeaveMinutes: null, leaveReason: '' }); }}>恢复自动扣减</button></>}
    </fieldset>
    <fieldset className="day-schedule-editor" disabled={disabled}><legend>当日作息</legend><label className="checkbox-field"><input type="checkbox" checked={Boolean(value.schedule)} onChange={e => onChange({ ...value, schedule: e.target.checked ? { workdayStart: day.schedule.workdayStart, workdayEnd: day.schedule.workdayEnd, lunchStart: day.schedule.lunchStart, lunchEnd: day.schedule.lunchEnd } : null })} />单独设置这一天</label>{value.schedule ? <ScheduleFields prefix="当日作息" value={value.schedule} onChange={schedule => onChange({ ...value, schedule })} disabled={disabled} /> : <p>按日期段或系统默认作息计算。</p>}</fieldset>
    {error && <p role="alert" className="warning-text">{error}</p>}
  </div>;
}
