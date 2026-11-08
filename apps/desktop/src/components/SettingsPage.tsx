import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import type { AppSettings, DayOverride, NativePathSelection } from '../types';
import { defaultSettings, hoursToMinutes, scheduleMinutes, validateSettings } from '../settings';
import { compactMinutes } from '../attendanceState';
import ScheduleFields from './ScheduleFields';
import { decodeNativePath } from '../nativePaths';

interface Props { value: AppSettings; onSave: (value: AppSettings) => void; onBack: () => void; onDirty: (dirty: boolean) => void; period?: { startDate: string; endDate: string }; dayRules: DayOverride[]; onSelectDay: (date: string) => void; }

/** 独立系统配置页；草稿保存成功之前不会影响现有统计。 */
export default function SettingsPage({ value, onSave, onBack, onDirty, period, dayRules, onSelectDay }: Props) {
  const [draft, setDraft] = useState<AppSettings>(() => structuredClone(value));
  const [standardHours, setStandardHours] = useState(String(value.calculation.standardMinutes / 60));
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(value) || standardHours !== String(value.calculation.standardMinutes / 60);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const calc = draft.calculation;
  const net = scheduleMinutes(calc);

  /** 执行本地持久化，失败保留完整草稿。 */
  function save() {
    const next = { ...draft, calculation: { ...calc, standardMinutes: hoursToMinutes(standardHours) } };
    const issue = validateSettings(next.calculation);
    if (issue) { setError(issue); return; }
    try { onSave(next); setDraft(next); setStandardHours(String(next.calculation.standardMinutes / 60)); setError(''); setSaved(true); } catch { setError('无法保存配置，请检查本地存储后重试；修改仍保留在本页。'); }
  }

  /** 确认离开时只丢弃本页草稿，不触及打卡及扣减修订。 */
  function back() { if (!dirty || window.confirm('系统配置尚未保存，是否放弃本页修改并返回？')) onBack(); }

  /** 原生目录选择使用与源文件相同的无损路径转换。 */
  async function selectOutput() {
    try { const selected = await invoke<NativePathSelection | null>('pick_output_directory'); if (selected) setDraft({ ...draft, outputRoot: decodeNativePath(selected) }); } catch (reason) { setError(String(reason)); }
  }

  return <section className="settings-page" aria-label="系统配置页面">
    <div className="settings-heading"><div><span className="section-label">PREFERENCES</span><h2>系统配置</h2><p>标准工时独立于作息时段，保存后重新统计生效。</p></div><button className="secondary-button" onClick={back}>返回工作台</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {saved && !dirty && <div className="change-banner" role="status">配置已保存。{period ? '当前统计仍使用原配置，返回后重新统计即可应用。' : '后续统计将使用这份配置。'}</div>}
    <section className="panel configuration-section"><h3>基础工时与默认作息</h3><p>未被日期规则覆盖的日期使用默认作息。更改时段不会自动更改标准工时。</p>
      <label className="standard-input">每日标准工时（小时）<input aria-label="每日标准工时（小时）" type="number" min="0" max="24" step="any" value={standardHours} onChange={e => { setStandardHours(e.target.value); setSaved(false); }} /></label>
      <ScheduleFields prefix="默认作息" value={calc} onChange={schedule => setDraft({ ...draft, calculation: { ...calc, ...schedule } })} />
      <div className="schedule-summary">作息净时长 <strong>{Number.isFinite(net) ? compactMinutes(net) : '待填写'}</strong><span>标准工时 <strong>{Number.isFinite(hoursToMinutes(standardHours)) ? compactMinutes(hoursToMinutes(standardHours)) : '待填写'}</strong></span></div>
      {Number.isFinite(net) && net !== hoursToMinutes(standardHours) && <p className="warning-text">作息净时长与标准工时不同。请假按作息分摊，加班与自动扣减按标准工时计算，不会自动补齐差额。</p>}
    </section>
    <section className="panel configuration-section"><div className="section-actions"><div><h3>日期段作息</h3><p>单日覆盖优先于日期段，日期段优先于默认。同级日期段不得重叠。</p></div><button className="secondary-button" onClick={() => setDraft({ ...draft, calculation: { ...calc, dateRules: [...calc.dateRules, { id: crypto.randomUUID(), name: '', startDate: '', endDate: '', workdayStart: calc.workdayStart, workdayEnd: calc.workdayEnd, lunchStart: calc.lunchStart, lunchEnd: calc.lunchEnd }] } })}>添加日期段</button></div>
      {!calc.dateRules.length && <p className="muted">暂无日期规则。单日例外可在每日考勤编辑中设置。</p>}
      {calc.dateRules.map((rule, index) => {
        const update = (patch: Partial<typeof rule>) => setDraft({ ...draft, calculation: { ...calc, dateRules: calc.dateRules.map(item => item.id === rule.id ? { ...item, ...patch } : item) } });
        return <div className="date-rule" key={rule.id}><div className="configuration-grid"><label>规则名称<input aria-label={'规则名称' + (index + 1)} value={rule.name} placeholder="例如：夏季作息" onChange={e => update({ name: e.target.value })} /></label><label>开始日期<input aria-label={'开始日期' + (index + 1)} type="date" value={rule.startDate} onChange={e => update({ startDate: e.target.value })} /></label><label>结束日期<input aria-label={'结束日期' + (index + 1)} type="date" value={rule.endDate} onChange={e => update({ endDate: e.target.value })} /></label><button className="text-button danger-text" onClick={() => setDraft({ ...draft, calculation: { ...calc, dateRules: calc.dateRules.filter(item => item.id !== rule.id) } })}>删除规则</button></div><ScheduleFields prefix={'日期段' + (index + 1)} value={rule} onChange={update} /></div>;
      })}
    </section>
    {!!dayRules.length && <section className="panel configuration-section"><h3>当前会话的单日作息</h3><p>以下规则与每日详情共用同一份数据，不写入系统默认配置。</p>{dayRules.map(rule => <div className="file-entry" key={rule.workDate}><span>{rule.workDate} · {rule.schedule?.workdayStart}—{rule.schedule?.workdayEnd} · {rule.schedule?.lunchStart ? '午休 ' + rule.schedule.lunchStart + '—' + rule.schedule.lunchEnd : '无午休'}</span><button className="text-button" onClick={() => { if (!dirty || window.confirm('系统配置尚未保存，是否放弃本页草稿并查看该日？')) onSelectDay(rule.workDate); }}>查看并编辑</button></div>)}</section>}
    <section className="panel configuration-section"><h3>导出位置</h3><p title={draft.outputRoot}>{draft.outputRoot || '第一个加入文件所在目录的 export_report 文件夹'}</p><div className="section-actions"><button className="secondary-button" onClick={selectOutput}>选择导出目录</button><button className="text-button" onClick={() => setDraft({ ...draft, outputRoot: '' })}>使用默认目录</button></div></section>
    <section className="panel configuration-section"><h3>计算与保存说明</h3><p>考勤日从 04:00 开始，不计地点福利。休息日不扣午休。手动调休扣减优先于自动计算。</p><p>系统配置保存在本机。每日打卡、扣减及单日作息修订仅在当前文件会话中保留，导出包含本次生效规则。</p>{period && <p>当前数据期间：{period.startDate} 至 {period.endDate}。标准工时影响全部已覆盖工作日；默认作息只影响没有日期段或单日覆盖的日期。</p>}</section>
    <div className="settings-footer"><span>{dirty ? '有尚未保存的配置' : '配置已与本地保存一致'}</span><button className="text-button" onClick={() => { setDraft(structuredClone(value)); setStandardHours(String(value.calculation.standardMinutes / 60)); setError(''); }}>取消修改</button><button className="secondary-button" onClick={() => { setDraft(defaultSettings()); setStandardHours('8'); setSaved(false); }}>恢复默认</button><button className="primary-button" disabled={!dirty} onClick={save}>保存配置</button></div>
  </section>;
}
