import { useEffect, useMemo, useRef, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import HelpDrawer from "./components/HelpDrawer";
import InsightDrawer, { type InsightKey } from "./components/InsightDrawer";
import { formatMinutes, formatTime } from "./formatters";
import { compactMinutes, needsAttention, originalOverride, validateOverride } from "./attendanceState";
import type { AttendanceDay, AttendanceResult, DayOverride, NativePathSelection } from "./types";

type DayFilter = "all" | "attention" | "leave" | "edited";
type Overrides = Record<string, DayOverride>;

/** 保留 Windows UTF-16 文件路径，避免中文路径在原生选择器中损坏。 */
function decodeNativePath(selection: NativePathSelection): string {
  if (selection.path !== null) return selection.path;
  if (!selection.pathUtf16) throw new Error("文件选择器没有返回有效路径");
  let path = "";
  for (let index = 0; index < selection.pathUtf16.length; index += 4096) path += String.fromCharCode(...selection.pathUtf16.slice(index, index + 4096));
  return path;
}

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
        <strong>{record.timestamp.replace("T", " ")} · {record.punch_type}</strong><p>{record.location || "未记录地点"}</p>{record.status && <small>源表状态：{record.status}</small>}
      </div>) : <p className="muted">当天没有原始打卡记录。</p>}
      <h4>原始调休申请</h4>
      {day.leaveRecords?.length ? day.leaveRecords.map((record, index) => <div className="source-item" key={record.sourceSheet + record.sourceRow + index}>
        <strong>源表填报 {formatMinutes(record.minutes)}</strong><p>{record.description}</p>
        {record.startAt && <p>{record.startAt.replace("T", " ")} 至 {record.endAt?.replace("T", " ")}</p>}
        <small>{record.sourceSheet} · 第 {record.sourceRow} 行</small>
        {record.parseWarning && <p className="warning-text">{record.parseWarning}</p>}
      </div>) : <p className="muted">当天没有调休申请。</p>}
    </div>
  </details>;
}

/** 考勤工作台：文件、规则、草稿和已成功导出的结果分别管理。 */
function App() {
  const [inputPath, setInputPath] = useState("");
  const [resultInputPath, setResultInputPath] = useState("");
  const [outputRoot, setOutputRoot] = useState("");
  const [result, setResult] = useState<AttendanceResult | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<DayFilter>("all");
  const [helpOpen, setHelpOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeInsight, setActiveInsight] = useState<InsightKey | null>(null);
  const [appVersion, setAppVersion] = useState("");
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState(false);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [drafts, setDrafts] = useState<Overrides>({});
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [lunchStart, setLunchStart] = useState("12:00");
  const [lunchEnd, setLunchEnd] = useState("13:30");
  const discardDialog = useRef<HTMLDialogElement>(null);
  const sourceChanged = Boolean(result && inputPath !== resultInputPath);
  const rulesChanged = Boolean(result && (lunchStart !== result.rules?.lunchStart || lunchEnd !== result.rules?.lunchEnd));
  const dirty = Object.keys(drafts).length > 0;
  const selectedDay = result?.days.find((day) => day.workDate === selectedDate) || result?.days[0] || null;
  const attentionCount = result?.days.filter(needsAttention).length || 0;
  const visibleDays = useMemo(() => result?.days.filter((day) => {
    if (filter === "attention") return needsAttention(day);
    if (filter === "leave") return Boolean(day.leaveRecords?.length || day.leaveRequestedMinutes);
    if (filter === "edited") return day.isEdited || Boolean(drafts[day.workDate]);
    return true;
  }) || [], [result, filter, drafts]);
  const selectedDraft = selectedDay && (drafts[selectedDay.workDate] || {
    workDate: selectedDay.workDate,
    firstCheckIn: selectedDay.firstCheckIn?.slice(0, 16) || null,
    lastCheckOut: selectedDay.lastCheckOut?.slice(0, 16) || null,
  });
  const draftError = selectedDraft && editing ? validateOverride(selectedDraft) : "";

  /** 选择新文件时先验证本地文件，旧结果保留至新统计成功。 */
  async function acceptPaths(paths: string[]) {
    if (loading || selecting) { setError("正在处理文件，请完成后再选择其他报表。"); return; }
    if (paths.length !== 1 || !/\.xlsx$/i.test(paths[0])) { setError("一次仅支持一个 .xlsx 文件，请勿拖入文件夹或其他格式。"); return; }
    setSelecting(true);
    try {
      await invoke("validate_input_file", { inputPath: paths[0] });
      if (dirty) setPendingPath(paths[0]);
      else { setInputPath(paths[0]); setEditing(false); setError(""); }
    } catch (reason) { setError(String(reason)); }
    finally { setSelecting(false); setDragging(false); }
  }

  // 原生事件只注册一次，引用最新状态避免异步回调使用过期的编辑草稿。
  const nativeState = useRef({ acceptPaths, dirty, loading });
  nativeState.current = { acceptPaths, dirty, loading };
  useEffect(() => {
    if (!isTauri()) return;
    getVersion().then(setAppVersion).catch(() => undefined);
    let disposed = false;
    const cleanups: Array<() => void> = [];
    getCurrentWindow().onDragDropEvent(({ payload }) => {
      if (payload.type === "drop") { setDragging(false); void nativeState.current.acceptPaths(payload.paths); }
      else setDragging(payload.type === "enter" || payload.type === "over");
    }).then((unlisten) => { if (disposed) unlisten(); else cleanups.push(unlisten); }).catch(() => setError("拖放监听未就绪，请使用“浏览文件”导入。"));
    getCurrentWindow().onCloseRequested((event) => {
      if (nativeState.current.dirty || nativeState.current.loading) {
        event.preventDefault();
        setError(nativeState.current.loading ? "正在统计，请完成后再关闭。" : "有尚未保存的时间修订，请保存或取消修改后再关闭。");
      }
    }).then((unlisten) => { if (disposed) unlisten(); else cleanups.push(unlisten); }).catch(() => undefined);
    return () => { disposed = true; cleanups.forEach((cleanup) => cleanup()); };
  }, []);

  useEffect(() => {
    if (pendingPath) discardDialog.current?.showModal();
    else discardDialog.current?.close();
  }, [pendingPath]);

  useEffect(() => {
    if (!helpOpen && !activeInsight) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") { setHelpOpen(false); setActiveInsight(null); } };
    window.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener("keydown", closeOnEscape); };
  }, [helpOpen, activeInsight]);

  /** 文件选择与拖放使用相同的校验和替换行为。 */
  async function chooseInput() {
    try {
      const selected = await invoke<NativePathSelection | null>("pick_excel_file");
      if (selected) await acceptPaths([decodeNativePath(selected)]);
    } catch (reason) { setError(String(reason)); }
  }

  /** 输出目录设置失败时保留现有结果与目录。 */
  async function chooseOutput() {
    try {
      const selected = await open({ directory: true, multiple: false });
      if (typeof selected === "string") setOutputRoot(selected);
    } catch (reason) { setError(String(reason)); }
  }

  /** 计算与自动导出均成功后，才替换已生效的结果与修订。 */
  async function calculate() {
    if (!inputPath || loading) return;
    const merged = sourceChanged ? {} : { ...overrides, ...drafts };
    for (const override of Object.values(merged)) {
      const problem = validateOverride(override);
      if (problem) { setError(override.workDate + "：" + problem); setSelectedDate(override.workDate); return; }
    }
    const toMinutes = (value: string) => Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5));
    if (!lunchStart || !lunchEnd || lunchStart >= lunchEnd || lunchStart <= "09:00" || lunchStart >= "17:00" || toMinutes(lunchEnd) - toMinutes(lunchStart) >= 420) {
      setError("午休开始需在 09:00 与 17:00 之间，结束晚于开始，且全天工作时段不能跨越午夜。"); setSettingsOpen(true); return;
    }
    setLoading(true); setError(""); setActiveInsight(null);
    try {
      const payload = await invoke<AttendanceResult>("calculate_attendance", { inputPath, outputRoot: outputRoot || null, overrides: Object.values(merged), lunchStart, lunchEnd });
      if (!payload.success) throw new Error("计算未完成，请检查源报表。");
      setResult(payload); setResultInputPath(inputPath); setOverrides(merged); setDrafts({}); setEditing(false);
      setSelectedDate((current) => payload.days.some((day) => day.workDate === current) ? current : payload.days[0]?.workDate || "");
    } catch (reason) { setError(String(reason)); }
    finally { setLoading(false); }
  }

  /** 保存每日覆盖草稿，空输入用于明确保留缺卡。 */
  function updateDraft(field: "firstCheckIn" | "lastCheckOut", value: string) {
    if (!selectedDraft) return;
    const updated = { ...selectedDraft, [field]: value || null };
    setDrafts((current) => ({ ...current, [updated.workDate]: updated }));
  }

  /** 把原始时间放入草稿；仍须保存重算才能成为有效结果。 */
  function restoreSelected() {
    if (!selectedDay) return;
    const restored = originalOverride(selectedDay);
    setDrafts((current) => ({ ...current, [restored.workDate]: restored }));
  }

  /** 从汇总钻取到每日详情，保留当前编辑草稿。 */
  function selectDay(workDate: string) {
    setSelectedDate(workDate); setActiveInsight(null); setFilter("all");
    document.getElementById("daily-workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return <main className="app-shell">
    <header className="app-header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">勤</span><div><h1>考勤工作台</h1><span>个人工时与调休统计{appVersion && " · v" + appVersion}</span></div></div>
      <div className="header-actions"><span className="local-label"><i />本地处理</span><button className="text-button" onClick={() => { setHelpOpen(true); setActiveInsight(null); }}>使用帮助</button></div>
    </header>

    <section className={"import-panel panel " + (dragging ? "dragging" : "")} aria-label="导入考勤报表" aria-busy={loading || selecting}>
      <div className="file-icon" aria-hidden="true">X</div>
      <div className="file-summary"><span className="section-label">源考勤报表</span><strong title={inputPath}>{inputPath ? inputPath.split(/[\\/]/).pop() : "将 Excel 拖到这里，或浏览文件"}</strong><small>{dragging ? "松开以选择报表" : "单个 .xlsx · 保留原始文件 · 统计成功后自动导出"}</small></div>
      <div className="import-actions"><button className="secondary-button" disabled={loading || selecting} onClick={chooseInput}>{selecting ? "正在检查…" : "浏览文件"}</button><button className="primary-button" disabled={!inputPath || loading || selecting} onClick={calculate}>{loading && <span className="loading-spinner" />}{loading ? "统计并导出中…" : dirty ? "保存并重算" : result ? "重新统计" : "开始统计"}</button></div>
    </section>

    <div className="rules-bar"><span>标准 <strong>8h</strong><i />考勤日 <strong>04:00—次日 03:59</strong><i />午休 <strong>{lunchStart}—{lunchEnd}</strong></span><button className="text-button" onClick={() => setSettingsOpen(!settingsOpen)} aria-expanded={settingsOpen}>计算与导出设置 {settingsOpen ? "⌃" : "⌄"}</button></div>
    {settingsOpen && <section className="settings-panel panel" aria-label="计算与导出设置">
      <fieldset disabled={loading || editing}><legend>工作日午休</legend><div className="time-settings"><label>开始<input type="time" value={lunchStart} onChange={(event) => setLunchStart(event.target.value)} /></label><span>至</span><label>结束<input type="time" value={lunchEnd} onChange={(event) => setLunchEnd(event.target.value)} /></label><button className="text-button" onClick={() => { setLunchStart("12:00"); setLunchEnd("13:30"); }}>恢复当前作息</button></div><p>默认 12:00–13:30。未来调整作息时可手动修改，正常工作时段随午休保持每天 8 小时。</p>{editing && <p>请先保存或取消时间编辑，再调整规则。</p>}</fieldset>
      <div className="output-setting"><span className="section-label">导出目录</span><button className="secondary-button" disabled={loading} onClick={chooseOutput}>选择目录</button><p title={outputRoot}>{outputRoot || "源文件旁的 export_report 文件夹"}</p></div>
    </section>}
    {error && <div className="error-banner" role="alert"><strong>请检查</strong><span>{error}</span><button onClick={() => setError("")} aria-label="关闭错误提示">×</button></div>}
    {(sourceChanged || rulesChanged || dirty) && result && <div className="change-banner" role="status"><strong>{sourceChanged ? "已选择新文件" : dirty ? Object.keys(drafts).length + " 天有未保存修订" : "规则待应用"}</strong><span>下方仍是上次成功统计的结果。{sourceChanged ? "请开始统计新报表。" : "保存并重新统计后更新结果及导出文件。"}</span></div>}

    {!result && <section className="empty-state panel"><div className="empty-illustration" aria-hidden="true"><span /><span /><span /></div><h2>从一份考勤报表开始</h2><p>查看工时与调休结余，核对异常日期，必要时修订打卡时间。</p><div className="empty-steps"><span><b>1</b> 导入报表</span><span><b>2</b> 核对每日记录</span><span><b>3</b> 保存统计结果</span></div></section>}

    {result && <>
      <section className="result-heading"><div><span className="section-label">{sourceChanged ? "上次统计" : "统计概览"}</span><h2>{result.period.startDate} <span>至</span> {result.period.endDate}</h2><p>{result.employee.name}<span> · {resultInputPath.split(/[\\/]/).pop()}</span></p></div><div className="result-meta"><span className="export-status">已导出本次结果</span><small title={result.outputPath}>{result.outputPath.split(/[\\/]/).pop()}</small><button className="text-button" onClick={() => setActiveInsight("calendar")}>查看工作日历来源</button></div></section>
      <section className="overview-grid" aria-label="统计汇总">
        <button className="metric-card net-card" onClick={() => setActiveInsight("net")}><span>净剩加班 <i>查看明细 ↗</i></span><strong>{compactMinutes(result.summary.netExtraMinutes, "待核对")}</strong><small>{result.summary.netExtraMinutes === null ? "存在未确定的调休，请先核对" : "加班时长 − 实际调休扣减"}</small></button>
        <button className="metric-card" onClick={() => setActiveInsight("overtime")}><span>加班时长</span><strong>{compactMinutes(result.summary.extraMinutes)}</strong><small>工作日超时与休息日工时</small></button>
        <button className="metric-card" onClick={() => setActiveInsight("leave")}><span>实际调休扣减</span><strong>{compactMinutes(result.summary.compensatoryLeaveMinutes, "待核对")}</strong><small>按工作时段及剩余工时核算</small></button>
        <button className={"metric-card attention-card " + (attentionCount ? "has-attention" : "")} onClick={() => { setFilter("attention"); document.getElementById("daily-workspace")?.scrollIntoView({ behavior: "smooth" }); }}><span>待处理日期</span><strong>{attentionCount}<em>天</em></strong><small>{attentionCount ? "查看缺卡与待核对记录 →" : "没有需要处理的记录"}</small></button>
      </section>
      <div className="secondary-metrics"><button onClick={() => setActiveInsight("required")}>应计标准 <strong>{compactMinutes(result.summary.requiredStandardMinutes)}</strong></button><button onClick={() => setActiveInsight("effective")}>实际有效 <strong>{compactMinutes(result.summary.actualEffectiveMinutes)}</strong></button><span>原始文件未修改</span></div>

      <section className="workspace-grid" id="daily-workspace">
        <div className="panel table-panel"><div className="panel-toolbar"><div><h3>每日明细</h3><span>{visibleDays.length} 天</span></div><button className={"secondary-button " + (editing ? "active" : "")} disabled={loading || sourceChanged} onClick={() => { if (editing) { setEditing(false); setDrafts({}); } else { setEditing(true); setSettingsOpen(false); } }}>{editing ? "取消编辑" : "编辑时间"}</button></div>
          <div className="table-filter" role="group" aria-label="筛选日期">{(["all", "attention", "leave", "edited"] as DayFilter[]).map((value) => <button key={value} className={filter === value ? "active" : ""} aria-pressed={filter === value} onClick={() => setFilter(value)}>{{ all: "全部", attention: "待处理", leave: "有调休", edited: "已修订" }[value]}{value === "attention" && attentionCount > 0 && <b>{attentionCount}</b>}</button>)}</div>
          {editing && <div className="editing-toolbar"><span>{dirty ? Object.keys(drafts).length + " 天待保存" : "选择日期，在右侧修订时间"}</span><button className="text-button" disabled={loading} onClick={() => { setDrafts({}); setEditing(false); }}>取消修改</button><button className="primary-button small" disabled={!dirty || loading} onClick={calculate}>保存并重算</button></div>}
          <div className="table-scroll"><table><thead><tr><th>日期 / 状态</th><th>上下班</th><th className="numeric">实际工时</th><th className="numeric">加班</th><th className="numeric">调休扣减</th><th className="numeric">净剩</th></tr></thead><tbody>{visibleDays.map((day) => <tr key={day.workDate} tabIndex={0} aria-selected={selectedDay?.workDate === day.workDate} className={selectedDay?.workDate === day.workDate ? "selected" : ""} onClick={() => setSelectedDate(day.workDate)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedDate(day.workDate); } }}>
            <td><strong>{day.workDate.slice(5)} <span className="weekday">周{day.weekday}</span></strong><small className={needsAttention(day) ? "warning-text" : "muted"}>{day.requiresReview ? "调休待核对" : needsAttention(day) ? day.status : day.requiredStandardMinutes === 0 ? "休息日" : "正常"}{(day.isEdited || drafts[day.workDate]) && <span className="edited-mark">{drafts[day.workDate] ? "待保存" : "已修订"}</span>}</small></td>
            <td className="punch-cell"><PunchTime value={day.firstCheckIn} workDate={day.workDate} /><span className="time-dash">—</span><PunchTime value={day.lastCheckOut} workDate={day.workDate} /></td><td className="numeric">{compactMinutes(day.actualEffectiveMinutes)}</td><td className="numeric">{compactMinutes(day.extraMinutes)}</td><td className="numeric">{compactMinutes(day.compensatoryLeaveMinutes, "待核对")}</td><td className={"numeric net-value " + ((day.netExtraMinutes || 0) < 0 ? "negative" : "")}>{compactMinutes(day.netExtraMinutes, "待核对")}</td>
          </tr>)}</tbody></table>{!visibleDays.length && <div className="table-empty">没有符合筛选条件的日期。</div>}</div>
        </div>
        <aside className="panel detail-panel" aria-label="所选日期详情">{selectedDay && <>
          <div className="detail-title"><div><span className="section-label">每日核对</span><h3>{selectedDay.workDate} <small>周{selectedDay.weekday}</small></h3><span className="muted">{selectedDay.dayType}</span></div><span className={"status " + (needsAttention(selectedDay) ? "warning" : "ok")}>{selectedDay.requiresReview ? "待核对" : selectedDay.requiredStandardMinutes === 0 && !selectedDay.records.length && !selectedDay.firstCheckIn && !selectedDay.lastCheckOut ? "休息" : selectedDay.status}</span></div>
          {editing && selectedDraft ? <div className="time-editor"><label>上班日期与时间<input type="datetime-local" step="60" value={selectedDraft.firstCheckIn || ""} disabled={loading} onChange={(event) => updateDraft("firstCheckIn", event.target.value)} /></label><label>下班日期与时间<input type="datetime-local" step="60" value={selectedDraft.lastCheckOut || ""} disabled={loading} onChange={(event) => updateDraft("lastCheckOut", event.target.value)} /></label><p>04:00 前归前一天。留空表示缺卡。</p>{draftError && <p className="warning-text" role="alert">{draftError}</p>}<button className="text-button" disabled={loading} onClick={restoreSelected}>恢复当天原始时间</button><small>原始：{selectedDay.originalFirstCheckIn?.replace("T", " ") || "缺卡"} — {selectedDay.originalLastCheckOut?.replace("T", " ") || "缺卡"}</small></div> : <div className="time-pair"><div><span>上班</span><strong><PunchTime value={selectedDay.firstCheckIn} workDate={selectedDay.workDate} /></strong></div><i>→</i><div><span>下班</span><strong><PunchTime value={selectedDay.lastCheckOut} workDate={selectedDay.workDate} /></strong></div></div>}
          {selectedDay.isEdited && <p className="edited-note">已使用手动修订时间，原始记录保留。</p>}
          <div className="calculation-list"><div><span>打卡跨度</span><strong>{formatMinutes(selectedDay.rawDurationMinutes)}</strong></div><div><span>午休扣除</span><strong>− {formatMinutes(selectedDay.lunchDeductionMinutes)}</strong></div><div className="calculation-subtotal"><span>实际有效工时</span><strong>{formatMinutes(selectedDay.actualEffectiveMinutes)}</strong></div><div><span>当日调休申请</span><strong>{selectedDay.leaveRequestedMinutes === null ? "待核对" : formatMinutes(selectedDay.leaveRequestedMinutes)}</strong></div><div><span>实际调休扣减</span><strong>{selectedDay.compensatoryLeaveMinutes === null ? "待核对" : formatMinutes(selectedDay.compensatoryLeaveMinutes)}</strong></div><div className="total net"><span>净剩加班</span><strong>{selectedDay.netExtraMinutes === null ? "待核对" : formatMinutes(selectedDay.netExtraMinutes)}</strong></div></div>
          <div className="notes"><h4>计算说明</h4>{selectedDay.messages.map((message, index) => <p key={index}>{message}</p>)}</div><SourceRecords day={selectedDay} />
        </>}</aside>
      </section>
      <InsightDrawer active={activeInsight} result={result} onClose={() => setActiveInsight(null)} onSelectDay={selectDay} onShowAbnormal={() => { setFilter("attention"); setActiveInsight(null); }} />
    </>}
    <footer className="workspace-footer">考勤数据仅在本机处理<span>按实际工时核算 · 不计地点福利</span></footer>
    <HelpDrawer open={helpOpen} version={appVersion || "开发版"} onClose={() => setHelpOpen(false)} />
    <dialog className="confirm-dialog" ref={discardDialog} onCancel={() => setPendingPath(null)}><h2>有尚未保存的修订</h2><p>更换文件将放弃当前时间草稿。上次已导出的文件不受影响。</p><div><button className="secondary-button" onClick={() => setPendingPath(null)}>继续编辑</button><button className="primary-button" onClick={() => { if (pendingPath) setInputPath(pendingPath); setDrafts({}); setEditing(false); setPendingPath(null); setError(""); }}>放弃草稿并更换</button></div></dialog>
  </main>;
}

export default App;
