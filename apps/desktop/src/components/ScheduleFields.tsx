import type { Schedule } from '../types';

interface Props { value: Schedule; onChange: (value: Schedule) => void; prefix: string; disabled?: boolean; }

/** 通用作息字段，默认、日期段与单日共用同一输入语义。 */
export default function ScheduleFields({ value, onChange, prefix, disabled }: Props) {
  return <fieldset className="schedule-fields" disabled={disabled}><legend>{prefix}</legend><div className="configuration-grid">
    <label>正常上班<input aria-label={prefix + '正常上班'} type="time" value={value.workdayStart} onChange={e => onChange({ ...value, workdayStart: e.target.value })} /></label>
    <label>正常下班<input aria-label={prefix + '正常下班'} type="time" value={value.workdayEnd} onChange={e => onChange({ ...value, workdayEnd: e.target.value })} /></label>
    <label className="checkbox-field"><input type="checkbox" checked={value.lunchStart === null} onChange={e => onChange({ ...value, lunchStart: e.target.checked ? null : '12:00', lunchEnd: e.target.checked ? null : '13:30' })} />无午休</label>
    {value.lunchStart !== null && <><label>午休开始<input aria-label={prefix + '午休开始'} type="time" value={value.lunchStart} onChange={e => onChange({ ...value, lunchStart: e.target.value })} /></label><label>午休结束<input aria-label={prefix + '午休结束'} type="time" value={value.lunchEnd || ''} onChange={e => onChange({ ...value, lunchEnd: e.target.value })} /></label></>}
  </div></fieldset>;
}
