import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import HelpDrawer from "./components/HelpDrawer";
import InsightDrawer, { type InsightKey } from "./components/InsightDrawer";
import { formatMinutes, formatTime } from "./formatters";
import { compactMinutes, needsAttention, validateOverride } from "./attendanceState";
import type { AppSettings, AttendanceDay, AttendanceResult, DayOverride, NativePathSelection, ProgressInfo } from "./types";

type DayFilter = "all" | "attention" | "leave" | "edited" | "missing" | "conflict";
type Overrides = Record<string, DayOverride>;

import SettingsPage from './components/SettingsPage';
import DayEditor from './components/DayEditor';
import { readSettings, saveSettings, validateSettings } from './settings';
import { decodeNativePath, pathUtf16 } from './nativePaths';

/** 日期标签始终保留跨日信息，不把次日下班伪装成当天。 */
function PunchTime({ value, workDate }: { value: string | null; workDate: string }) {
  if (!value) return <span className="muted">缺卡</span>;
  return <span className="punch-time">{formatTime(value)}{value.slice(0, 10) !== workDate && <em>次日</em>}</span>;
}

/** 展示一天的原始申请和打卡来源，不替换或改写源文件。 */
function SourceRecords({ day }: { day: AttendanceDay }) {
  return <details className="source-records" key={day.workDate}>
    <summary>查看原始记录 <span>{day.records.length} 条打卡 · {day.leaveRecords?.length || 0} 条申请</span></summary>
    <div className="source-content">
      <h4>原始打卡</h4>
      {day.records.length ? day.records.map((record, index) => <div className="source-item" key={record.timestamp + index}>
        <strong>{record.timestamp.replace("T", " ")} · {record.punch_type}</strong><p>{record.location || "未记录地点"}</p>{record.status && <small>源表状态：{record.status}</small>}{record.sources?.map((source, sourceIndex) => <small key={source.sourceId + sourceIndex} title={source.path}>{source.name} · {source.sourceSheet} · 第 {source.sourceRow} 行</small>)}
      </div>) : <p className="muted">当天没有原始打卡记录。</p>}
      <h4>原始调休申请</h4>
      {day.leaveRecords?.length ? day.leaveRecords.map((record, index) => <div className="source-item" key={record.sourceSheet + record.sourceRow + index}>
        <strong>源表填报 {formatMinutes(record.minutes)}</strong><p>{record.description}</p>
        {record.startAt && <p>{record.startAt.replace("T", " ")} 至 {record.endAt?.replace("T", " ")}</p>}
        {record.sources?.length ? record.sources.map((source, sourceIndex) => <small key={source.sourceId + sourceIndex} title={source.path}>{source.name} · {source.sourceSheet} · 第 {source.sourceRow} 行</small>) : <small>{record.sourceSheet} · 第 {record.sourceRow} 行</small>}
        {record.parseWarning && <p className="warning-text">{record.parseWarning}</p>}
      </div>) : <p className="muted">当天没有调休申请。</p>}
    </div>
  </details>;
}

/** 考勤工作台：文件集、系统配置、修订草稿与已计算快照独立管理。 */
function App() {
  const [initial] = useState(() => readSettings(localStorage));
  const [settings, setSettings] = useState(initial.settings);
  const [configDirty, setConfigDirty] = useState(false);
  const [files, setFiles] = useState<Array<{ path: string; error?: string }>>([]);
  const [resultPaths, setResultPaths] = useState<string[]>([]);
  const [resultSettings, setResultSettings] = useState('');
  const [result, setResult] = useState<AttendanceResult | null>(null);
  const [selectedDate, setSelectedDate] = useState('');
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState(initial.error);
  const [notice, setNotice] = useState('');
  const [filter, setFilter] = useState<DayFilter>('all');
  const [helpOpen, setHelpOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeInsight, setActiveInsight] = useState<InsightKey | null>(null);
  const [appVersion, setAppVersion] = useState('');
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState(false);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [drafts, setDrafts] = useState<Overrides>({});
  const [sourceChoices, setSourceChoices] = useState<Record<string, string>>({});
  const [choicesChanged, setChoicesChanged] = useState(false);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);
  const cancellation = useRef(false);
  const paths = files.map(file => file.path);
  const sourceChanged = Boolean(result && JSON.stringify(paths) !== JSON.stringify(resultPaths));
  const rulesChanged = Boolean(result && JSON.stringify(settings) !== resultSettings);
  const dirty = Object.keys(drafts).length > 0;
  const selectedDay = result?.days.find(day => day.workDate === selectedDate) || result?.days[0] || null;
  const attentionCount = result?.days.filter(needsAttention).length || 0;
  const visibleDays = useMemo(() => result?.days.filter(day => {
    if (filter === 'attention') return needsAttention(day);
    if (filter === 'leave') return Boolean(day.leaveRecords?.length || day.leaveRequestedMinutes || day.isLeaveEdited);
    if (filter === 'edited') return day.isEdited || Boolean(drafts[day.workDate]);
    if (filter === 'missing') return day.covered === false;
    if (filter === 'conflict') return result.conflicts?.some(item => item.workDate === day.workDate && !item.resolved);
    return true;
  }) || [], [result, filter, drafts]);
  const selectedDraft = selectedDay ? drafts[selectedDay.workDate] || overrides[selectedDay.workDate] || { workDate: selectedDay.workDate } : null;
  const setConfigDirtyStable = useCallback((value: boolean) => setConfigDirty(value), []);

  /** 批量逐项校验，失败文件保留在列表中，禁止静默略过。 */
  async function acceptPaths(incoming: string[]) {
    if (loading || selecting) { setError('正在处理文件，请稍后再添加报表。'); return; }
    if (!incoming.length) return;
    setSelecting(true); setError('');
    const entries: Array<{ path: string; error?: string }> = [];
    try {
      for (const path of [...new Set(incoming)]) {
        if (paths.includes(path)) { setNotice('重复路径已忽略，同一文件只加入一次。'); continue; }
        try {
          if (!/\.xlsx$/i.test(path)) throw new Error('仅支持 .xlsx 文件，不支持文件夹或其他格式。');
          await invoke('validate_input_file', { inputPath: path, inputPathUtf16: pathUtf16(path) });
          entries.push({ path });
        } catch (reason) { entries.push({ path, error: String(reason) }); }
      }
      setFiles(current => [...current, ...entries.filter(item => !current.some(file => file.path === item.path))]);
    } finally { setSelecting(false); setDragging(false); }
  }

  /** 移除文件前明确处理草稿及已不再覆盖日期的修订。 */
  function removeFile(path: string) {
    if (loading || selecting) return;
    const next = files.filter(file => file.path !== path);
    if (!window.confirm('移除此文件后需要重新统计。已不在剩余文件覆盖范围内的修订将被移除，是否继续？')) return;
    const remaining = result?.sources.filter(source => next.some(file => file.path === source.path)) || [];
    const keep = (items: Overrides) => Object.fromEntries(Object.entries(items).filter(([date]) => remaining.some(source => source.startDate <= date && source.endDate >= date)));
    setFiles(next); setOverrides(keep(overrides)); setDrafts(keep(drafts)); setSourceChoices({}); setChoicesChanged(false);
    setNotice('文件已移除；失去对应数据的修订已清理，请重新统计。');
    if (!next.length) { setResult(null); setResultPaths([]); setEditing(false); }
  }

  const nativeState = useRef({ acceptPaths, dirty, configDirty, loading });
  nativeState.current = { acceptPaths, dirty, configDirty, loading };
  useEffect(() => {
    if (!isTauri()) return;
    getVersion().then(setAppVersion).catch(() => undefined);
    let disposed = false;
    const cleanups: Array<() => void> = [];
    const register = (promise: Promise<() => void>) => promise.then(fn => { if (disposed) fn(); else cleanups.push(fn); }).catch(reason => setError(String(reason)));
    register(listen<{ paths: NativePathSelection[] }>('native-file-drop', ({ payload }) => { setDragging(false); void nativeState.current.acceptPaths(payload.paths.map(decodeNativePath)); }));
    register(listen<ProgressInfo>('calculation-progress', ({ payload }) => setProgress(payload)));
    register(getCurrentWindow().onDragDropEvent(({ payload }) => setDragging(payload.type === 'enter' || payload.type === 'over')));
    register(getCurrentWindow().onCloseRequested(event => {
      if (nativeState.current.dirty || nativeState.current.configDirty || nativeState.current.loading) { event.preventDefault(); setError('有未保存的修改或正在进行的统计，请保存、取消修改或取消任务后再关闭。'); }
    }));
    return () => { disposed = true; cleanups.forEach(fn => fn()); };
  }, []);

  useEffect(() => {
    if (!helpOpen && !activeInsight) return;
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') { setHelpOpen(false); setActiveInsight(null); } };
    window.addEventListener('keydown', close);
    return () => { document.body.style.overflow = overflow; window.removeEventListener('keydown', close); };
  }, [helpOpen, activeInsight]);

  /** 多选浏览与原生拖放共用追加和校验规则。 */
  async function chooseInput() {
    try { const selected = await invoke<NativePathSelection[] | null>('pick_excel_files'); if (selected) await acceptPaths(selected.map(decodeNativePath)); } catch (reason) { setError(String(reason)); }
  }

  /** 原生取消会终止本次计算连接，下一次统计自动恢复引擎。 */
  async function cancel() {
    try { cancellation.current = true; await invoke('cancel_calculation'); setNotice('已取消本次统计，上一份结果与修改保留。'); } catch (reason) { cancellation.current = false; setError(String(reason)); }
  }

  /** 统一发送文件、规则及覆盖值；失败或取消时保留原结果与草稿。 */
  async function calculate() {
    if (!paths.length || loading || selecting) return;
    if (files.some(file => file.error)) { setError('请先移除或替换文件列表中校验失败的文件。'); return; }
    const merged = { ...overrides, ...drafts };
    for (const override of Object.values(merged)) { const problem = validateOverride(override); if (problem) { setError(override.workDate + '：' + problem); setSelectedDate(override.workDate); return; } }
    const problem = validateSettings(settings.calculation);
    if (problem) { setError(problem); setSettingsOpen(true); return; }
    setLoading(true); setError(''); setNotice(''); setActiveInsight(null); setProgress({ stage: 'starting', message: '准备读取报表…' }); cancellation.current = false;
    try {
      const payload = await invoke<AttendanceResult>('calculate_attendance', { inputPaths: paths, inputPathsUtf16: paths.map(pathUtf16), outputRoot: settings.outputRoot || null, outputRootUtf16: settings.outputRoot ? pathUtf16(settings.outputRoot) : null, settings: settings.calculation, overrides: Object.values(merged), sourceChoices, expectedEmployee: result && Object.keys(merged).length ? result.employee : null });
      if (cancellation.current) return;
      if (!payload.success) throw new Error('计算未完成，请检查报表。');
      setResult(payload); setResultPaths(paths); setResultSettings(JSON.stringify(settings)); setChoicesChanged(false);
      if (!payload.exportBlocked) { setOverrides(merged); setDrafts({}); setEditing(false); }
      else setNotice('存在来源冲突，尚未导出。请选择每个日期采用的来源后重新统计。');
      setSelectedDate(current => payload.days.some(day => day.workDate === current) ? current : payload.days[0]?.workDate || '');
    } catch (reason) { if (!cancellation.current) setError(String(reason)); }
    finally { setLoading(false); setProgress(null); }
  }

  /** 从汇总返回日期，保留草稿及当前文件集。 */
  function selectDay(workDate: string) { setSelectedDate(workDate); setActiveInsight(null); setFilter('all'); document.getElementById('daily-workspace')?.scrollIntoView({ behavior: 'smooth' }); }

  /** 配置先持久化再更新状态，写入失败不会替换已生效设置。 */
  function applySettings(value: AppSettings) { saveSettings(localStorage, value); setSettings(value); }

  return <main className="app-shell">
    <header className="app-header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">勤</span><div><h1>考勤工作台</h1><span>个人工时与调休统计{appVersion && " · v" + appVersion}</span></div></div>
      <div className="header-actions"><button className="secondary-button" disabled={loading || selecting || settingsOpen} onClick={() => setSettingsOpen(true)}>系统配置</button><span className="local-label"><i />本地处理</span><button className="text-button" onClick={() => { setHelpOpen(true); setActiveInsight(null); }}>使用帮助</button></div>
    </header>

    {settingsOpen ? <SettingsPage value={settings} onSave={applySettings} onBack={() => { setSettingsOpen(false); setConfigDirty(false); }} onDirty={setConfigDirtyStable} period={result?.period} dayRules={Object.values({ ...overrides, ...drafts }).filter(value => value.schedule)} onSelectDay={date => { setSettingsOpen(false); setSelectedDate(date); setEditing(true); }} /> : <>
    <section className={"import-panel panel " + (dragging ? "dragging" : "")} aria-label="导入考勤报表" aria-busy={loading || selecting}>
      <div className="file-icon" aria-hidden="true">X</div><div className="file-summary"><span className="section-label">考勤文件集</span><strong>{paths.length ? '已添加 ' + paths.length + ' 份报表' : '拖入多份 Excel，按时间合并统计'}</strong><small>{dragging ? '松开以追加文件' : '支持同一员工的多月 .xlsx · 保留原始文件'}</small></div>
      <div className="import-actions"><button className="secondary-button" disabled={loading || selecting} onClick={chooseInput}>{selecting ? '正在检查…' : '添加报表'}</button><button className="primary-button" disabled={!paths.length || loading || selecting || files.some(file => file.error)} onClick={calculate}>{loading && <span className="loading-spinner" />}{loading ? '统计并导出中…' : dirty ? '保存并重算' : result ? '重新统计' : '开始统计'}</button></div>
    </section>
    {!!files.length && <section className="panel file-list" aria-label="报表文件列表">{files.map(file => { const source = result?.sources?.find(item => item.path === file.path); return <div className="file-entry" key={file.path}><div><strong title={file.path}>{file.path.split(/[\\/]/).pop()}</strong><small title={file.path}>{file.error || (source ? source.startDate + ' 至 ' + source.endDate + (source.duplicateOf ? ' · 内容重复，已去重' : source.cacheHit ? ' · 已复用解析缓存' : '') : '待解析')}</small></div><button className="text-button danger-text" disabled={loading || selecting} aria-label={'移除 ' + file.path.split(/[\\/]/).pop()} onClick={() => removeFile(file.path)}>移除</button></div>; })}</section>}
    <div className="rules-bar"><span>标准 <strong>{compactMinutes(settings.calculation.standardMinutes)}</strong><i />考勤日 <strong>04:00—次日 03:59</strong><i />默认作息 <strong>{settings.calculation.workdayStart}—{settings.calculation.workdayEnd}</strong></span><button className="text-button" disabled={loading} onClick={() => setSettingsOpen(true)}>管理系统配置 →</button></div>
    {loading && <div className="progress-banner" role="status"><span>{progress?.message || '正在统计…'}{progress?.total ? ' (' + progress.current + '/' + progress.total + ')' : ''}</span><button className="secondary-button" onClick={cancel}>取消本次统计</button></div>}
    {notice && <div className="change-banner" role="status">{notice}</div>}
    {error && <div className="error-banner" role="alert"><strong>请检查</strong><span>{error}</span><button onClick={() => setError("")} aria-label="关闭错误提示">×</button></div>}
    {(sourceChanged || rulesChanged || dirty || choicesChanged) && result && <div className="change-banner" role="status"><strong>{sourceChanged ? "文件集已变更" : dirty ? Object.keys(drafts).length + " 天有未保存修订" : "规则待应用"}</strong><span>下方仍是上次成功统计的结果。{sourceChanged ? "请重新统计当前文件集。" : "保存并重新统计后更新结果及导出文件。"}</span></div>}

    {!result && <section className="empty-state panel"><div className="empty-illustration" aria-hidden="true"><span /><span /><span /></div><h2>从一份考勤报表开始</h2><p>查看工时与调休结余，核对异常日期，必要时修订打卡时间。</p><div className="empty-steps"><span><b>1</b> 导入报表</span><span><b>2</b> 核对每日记录</span><span><b>3</b> 保存统计结果</span></div></section>}

    {result && <>
      <section className="result-heading"><div><span className="section-label">{sourceChanged ? "上次统计" : "统计概览"}</span><h2>{result.period.startDate} <span>至</span> {result.period.endDate}</h2><p>{result.employee.name}<span> · {resultPaths.length + ' 份报表'}</span></p></div><div className="result-meta"><span className="export-status">{result.outputPath ? "已导出本次结果" : "冲突待处理，尚未导出"}</span>{result.outputPath && <small title={result.outputPath}>{result.outputPath.split(/[\\/]/).pop()}</small>}<button className="text-button" onClick={() => setActiveInsight("calendar")}>查看工作日历来源</button></div></section>
      <section className="overview-grid" aria-label="统计汇总">
        <button className="metric-card net-card" onClick={() => setActiveInsight("net")}><span>{result.summary.isPartial ? "已覆盖期间净剩" : "净剩加班"} <i>查看明细 ↗</i></span><strong>{compactMinutes(result.summary.netExtraMinutes, "待核对")}</strong><small>{result.summary.netExtraMinutes === null ? "存在未确定的调休，请先核对" : "加班时长 − 实际调休扣减"}</small></button>
        <button className="metric-card" onClick={() => setActiveInsight("overtime")}><span>加班时长</span><strong>{compactMinutes(result.summary.extraMinutes)}</strong><small>工作日超时与休息日工时</small></button>
        <button className="metric-card" onClick={() => setActiveInsight("leave")}><span>实际调休扣减</span><strong>{compactMinutes(result.summary.compensatoryLeaveMinutes, "待核对")}</strong><small>按工作时段及剩余工时核算</small></button>
        <button className={"metric-card attention-card " + (attentionCount ? "has-attention" : "")} onClick={() => { setFilter("attention"); document.getElementById("daily-workspace")?.scrollIntoView({ behavior: "smooth" }); }}><span>待处理日期</span><strong>{attentionCount}<em>天</em></strong><small>{attentionCount ? "查看缺卡与待核对记录 →" : "没有需要处理的记录"}</small></button>
      </section>
      <div className="secondary-metrics"><button onClick={() => setActiveInsight("required")}>应计标准 <strong>{compactMinutes(result.summary.requiredStandardMinutes)}</strong></button><button onClick={() => setActiveInsight("effective")}>实际有效 <strong>{compactMinutes(result.summary.actualEffectiveMinutes)}</strong></button><span>原始文件未修改</span></div>

      {result.coverage?.missingDates.length > 0 && <div className="change-banner" role="status">有 {result.coverage.missingDates.length} 天未导入资料。汇总仅含已覆盖日期，不能视作完整期间。<button className="text-button" onClick={() => setFilter('missing')}>查看缺口</button></div>}
      {!!result.conflicts?.filter(item => !item.resolved).length && <section className="panel conflict-panel" aria-label="来源冲突"><h3>选择冲突日期采用的来源</h3>{result.conflicts.filter(item => !item.resolved).map(conflict => <label key={conflict.workDate}>{conflict.workDate} · {conflict.reason}<select aria-label={conflict.workDate + '采用来源'} value={sourceChoices[conflict.workDate] || ''} onChange={event => { setSourceChoices(current => ({ ...current, [conflict.workDate]: event.target.value })); setChoicesChanged(true); }}><option value="">请选择，随后重新统计</option>{conflict.sourceIds.map(id => <option key={id} value={id}>{result.sources.find(source => source.id === id)?.name || id}</option>)}</select></label>)}</section>}
      {result.monthlySummaries?.length > 1 && <details className="panel monthly-panel"><summary>按月汇总 · {result.monthlySummaries.length} 个月</summary><div className="table-scroll"><table><thead><tr><th>月份</th><th>标准工时</th><th>实际工时</th><th>加班</th><th>调休扣减</th><th>净剩</th></tr></thead><tbody>{result.monthlySummaries.map(month => <tr key={month.month}><td>{month.month}{month.isPartial && ' · 不完整'}</td><td>{compactMinutes(month.requiredStandardMinutes)}</td><td>{compactMinutes(month.actualEffectiveMinutes)}</td><td>{compactMinutes(month.extraMinutes)}</td><td>{compactMinutes(month.compensatoryLeaveMinutes, '待核对')}</td><td>{compactMinutes(month.netExtraMinutes, '待核对')}</td></tr>)}</tbody></table></div></details>}
      <section className="workspace-grid" id="daily-workspace">
        <div className="panel table-panel"><div className="panel-toolbar"><div><h3>每日明细</h3><span>{visibleDays.length} 天</span></div><button className={"secondary-button " + (editing ? "active" : "")} disabled={loading || sourceChanged} onClick={() => { if (editing) { setEditing(false); setDrafts({}); } else { setEditing(true); } }}>{editing ? "取消编辑" : "编辑考勤"}</button></div>
          <div className="table-filter" role="group" aria-label="筛选日期">{(["all", "attention", "leave", "edited", "missing", "conflict"] as DayFilter[]).map((value) => <button key={value} className={filter === value ? "active" : ""} aria-pressed={filter === value} onClick={() => setFilter(value)}>{{ all: "全部", attention: "待处理", leave: "有调休", edited: "已修订", missing: "未导入", conflict: "来源冲突" }[value]}{value === "attention" && attentionCount > 0 && <b>{attentionCount}</b>}</button>)}</div>
          {editing && <div className="editing-toolbar"><span>{dirty ? Object.keys(drafts).length + " 天待保存" : "选择日期，在右侧修订时间"}</span><button className="text-button" disabled={loading} onClick={() => { setDrafts({}); setEditing(false); }}>取消修改</button><button className="primary-button small" disabled={!dirty || loading} onClick={calculate}>保存并重算</button></div>}
          <div className="table-scroll"><table><thead><tr><th>日期 / 状态</th><th>上下班</th><th className="numeric">实际工时</th><th className="numeric">加班</th><th className="numeric">调休扣减</th><th className="numeric">净剩</th></tr></thead><tbody>{visibleDays.map((day) => <tr key={day.workDate} tabIndex={0} aria-selected={selectedDay?.workDate === day.workDate} className={selectedDay?.workDate === day.workDate ? "selected" : ""} onClick={() => setSelectedDate(day.workDate)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedDate(day.workDate); } }}>
            <td><strong>{day.workDate.slice(5)} <span className="weekday">周{day.weekday}</span></strong><small className={needsAttention(day) ? "warning-text" : "muted"}>{day.covered === false ? "未导入" : day.requiresReview ? "待核对" : needsAttention(day) ? day.status : day.requiredStandardMinutes === 0 ? "休息日" : "正常"}{(day.isEdited || drafts[day.workDate]) && <span className="edited-mark">{drafts[day.workDate] ? "待保存" : "已修订"}</span>}</small></td>
            <td className="punch-cell"><PunchTime value={day.firstCheckIn} workDate={day.workDate} /><span className="time-dash">—</span><PunchTime value={day.lastCheckOut} workDate={day.workDate} /></td><td className="numeric">{compactMinutes(day.actualEffectiveMinutes)}</td><td className="numeric">{compactMinutes(day.extraMinutes)}</td><td className="numeric">{compactMinutes(day.compensatoryLeaveMinutes, "待核对")}</td><td className={"numeric net-value " + ((day.netExtraMinutes || 0) < 0 ? "negative" : "")}>{compactMinutes(day.netExtraMinutes, "待核对")}</td>
          </tr>)}</tbody></table>{!visibleDays.length && <div className="table-empty">没有符合筛选条件的日期。</div>}</div>
        </div>
        <aside className="panel detail-panel" aria-label="所选日期详情">{selectedDay && <>
          <div className="detail-title"><div><span className="section-label">每日核对</span><h3>{selectedDay.workDate} <small>周{selectedDay.weekday}</small></h3><span className="muted">{selectedDay.dayType}</span></div><span className={"status " + (needsAttention(selectedDay) ? "warning" : "ok")}>{selectedDay.covered === false ? "未导入" : selectedDay.requiresReview ? "待核对" : selectedDay.requiredStandardMinutes === 0 && !selectedDay.records.length && !selectedDay.firstCheckIn && !selectedDay.lastCheckOut ? "休息" : selectedDay.status}</span></div>
          {selectedDay.covered !== false && editing && selectedDraft ? <DayEditor key={selectedDay.workDate} day={selectedDay} value={selectedDraft} onChange={value => setDrafts(current => ({ ...current, [value.workDate]: value }))} disabled={loading} /> : <div className="time-pair"><div><span>上班</span><strong><PunchTime value={selectedDay.firstCheckIn} workDate={selectedDay.workDate} /></strong></div><i>→</i><div><span>下班</span><strong><PunchTime value={selectedDay.lastCheckOut} workDate={selectedDay.workDate} /></strong></div></div>}
          {selectedDay.schedule && <div className="day-rule-note"><strong>{selectedDay.scheduleSource || '默认规则'}</strong><p>上班 {selectedDay.schedule.workdayStart} · 下班 {selectedDay.schedule.workdayEnd}<br />{selectedDay.schedule.lunchStart ? '午休 ' + selectedDay.schedule.lunchStart + '—' + selectedDay.schedule.lunchEnd : '无午休'} · 标准 {compactMinutes(selectedDay.schedule.standardMinutes)}</p></div>}
          {selectedDay.isLeaveEdited && <p className="edited-note">手动扣减：{compactMinutes(selectedDay.compensatoryLeaveMinutes)} · {selectedDay.leaveReason}</p>}
          {selectedDay.isEdited && <p className="edited-note">已应用人工修订，原始记录保留。</p>}
          <div className="calculation-list"><div><span>打卡跨度</span><strong>{formatMinutes(selectedDay.rawDurationMinutes)}</strong></div><div><span>午休扣除</span><strong>− {formatMinutes(selectedDay.lunchDeductionMinutes)}</strong></div><div className="calculation-subtotal"><span>实际有效工时</span><strong>{formatMinutes(selectedDay.actualEffectiveMinutes)}</strong></div><div><span>当日调休申请</span><strong>{selectedDay.leaveRequestedMinutes === null ? "待核对" : formatMinutes(selectedDay.leaveRequestedMinutes)}</strong></div><div><span>实际调休扣减</span><strong>{selectedDay.compensatoryLeaveMinutes === null ? "待核对" : formatMinutes(selectedDay.compensatoryLeaveMinutes)}</strong></div><div className="total net"><span>净剩加班</span><strong>{selectedDay.netExtraMinutes === null ? "待核对" : formatMinutes(selectedDay.netExtraMinutes)}</strong></div></div>
          <div className="notes"><h4>计算说明</h4>{selectedDay.messages.map((message, index) => <p key={index}>{message}</p>)}</div><SourceRecords day={selectedDay} />
        </>}</aside>
      </section>
      <InsightDrawer active={activeInsight} result={result} onClose={() => setActiveInsight(null)} onSelectDay={selectDay} onShowAbnormal={() => { setFilter("attention"); setActiveInsight(null); }} />
    </>}
    </>}
    <footer className="workspace-footer">考勤数据仅在本机处理<span>按实际工时核算 · 不计地点福利</span></footer>
    <HelpDrawer open={helpOpen} version={appVersion || "开发版"} onClose={() => setHelpOpen(false)} />
  </main>;
}

export default App;
