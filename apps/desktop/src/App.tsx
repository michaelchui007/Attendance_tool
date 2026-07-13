import { useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type { AttendanceDay, AttendanceResult } from "./types";

function formatMinutes(value: number | null): string {
  if (value === null) return "未计算";
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return `${hours}小时${String(minutes).padStart(2, "0")}分钟`;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return value.slice(11, 16);
}

function App() {
  const [inputPath, setInputPath] = useState("");
  const [outputRoot, setOutputRoot] = useState("");
  const [result, setResult] = useState<AttendanceResult | null>(null);
  const [selectedDay, setSelectedDay] = useState<AttendanceDay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | "normal" | "abnormal">("all");

  const visibleDays = useMemo(() => {
    if (!result) return [];
    if (filter === "normal") return result.days.filter((day) => day.status === "正常");
    if (filter === "abnormal") return result.days.filter((day) => day.status !== "正常");
    return result.days;
  }, [result, filter]);

  async function chooseInput() {
    const selected = await open({ multiple: false, filters: [{ name: "Excel", extensions: ["xlsx"] }] });
    if (typeof selected === "string") {
      setInputPath(selected);
      setResult(null);
      setSelectedDay(null);
      setError("");
    }
  }

  async function chooseOutput() {
    const selected = await open({ directory: true, multiple: false });
    if (typeof selected === "string") setOutputRoot(selected);
  }

  async function calculate() {
    if (!inputPath) return;
    setLoading(true);
    setError("");
    try {
      const payload = await invoke<AttendanceResult>("calculate_attendance", {
        inputPath,
        outputRoot: outputRoot || null,
      });
      setResult(payload);
      setSelectedDay(payload.days[0] ?? null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">ATTENDANCE WORKSPACE</p>
          <h1>考勤统计</h1>
          <p className="hero-copy">导入员工个人考勤报表，自动计算应计标准工时、实际有效工时与加班/调休时长。</p>
        </div>
        <div className="hero-badge">本地处理 · 数据不上传</div>
      </header>

      <section className="import-panel panel">
        <div className="file-field">
          <span className="field-label">源考勤报表</span>
          <button className="file-button" onClick={chooseInput}>
            <span>{inputPath ? inputPath.split(/[\\/]/).pop() : "选择 .xlsx 文件"}</span>
            <strong>浏览</strong>
          </button>
          {inputPath && <small title={inputPath}>{inputPath}</small>}
        </div>
        <div className="file-field">
          <span className="field-label">输出目录（可选）</span>
          <button className="file-button secondary" onClick={chooseOutput}>
            <span>{outputRoot ? outputRoot.split(/[\\/]/).pop() : "默认在源文件旁创建 export_report"}</span>
            <strong>选择</strong>
          </button>
          {outputRoot && <small title={outputRoot}>{outputRoot}</small>}
        </div>
        <button className="primary-button" disabled={!inputPath || loading} onClick={calculate}>
          {loading ? "正在计算…" : "开始统计"}
        </button>
      </section>

      {error && <div className="error-banner"><strong>处理失败</strong><span>{error}</span></div>}

      {!result && !error && (
        <section className="empty-state panel">
          <div className="empty-icon">↗</div>
          <h2>选择报表开始统计</h2>
          <p>支持包含“概况统计与打卡明细”和“打卡详情”的员工个人报表。</p>
        </section>
      )}

      {result && (
        <>
          <section className="result-heading">
            <div>
              <p className="eyebrow">{result.period.startDate} — {result.period.endDate}</p>
              <h2>{result.employee.name} <span>{result.employee.account}</span></h2>
            </div>
            <div className="export-note">已导出：<span title={result.outputPath}>{result.outputPath}</span></div>
          </section>

          <section className="kpi-grid">
            <article className="kpi-card"><span>应计标准工时</span><strong>{formatMinutes(result.summary.requiredStandardMinutes)}</strong><small>按工作日历累计</small></article>
            <article className="kpi-card accent"><span>实际有效工时</span><strong>{formatMinutes(result.summary.actualEffectiveMinutes)}</strong><small>仅汇总打卡完整日期</small></article>
            <article className="kpi-card warm"><span>加班/调休时长</span><strong>{formatMinutes(result.summary.extraMinutes)}</strong><small>工作日超 8 小时及休息日</small></article>
            <article className="kpi-card danger"><span>异常日期</span><strong>{result.summary.abnormalDayCount} 天</strong><small>缺卡或时间范围异常</small></article>
          </section>

          <section className="workspace-grid">
            <div className="panel table-panel">
              <div className="panel-toolbar">
                <div><h3>每日明细</h3><span>{visibleDays.length} 条记录</span></div>
                <div className="segmented">
                  {(["all", "normal", "abnormal"] as const).map((value) => (
                    <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>
                      {{ all: "全部", normal: "正常", abnormal: "异常" }[value]}
                    </button>
                  ))}
                </div>
              </div>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>日期</th><th>类型</th><th>状态</th><th>上下班</th><th>实际有效</th><th>加班/调休</th></tr></thead>
                  <tbody>
                    {visibleDays.map((day) => (
                      <tr key={day.workDate} className={selectedDay?.workDate === day.workDate ? "selected" : ""} onClick={() => setSelectedDay(day)}>
                        <td><strong>{day.workDate.slice(5)}</strong><small>星期{day.weekday}</small></td>
                        <td><span className={`day-type ${day.dayType.includes("工作日") ? "workday" : "rest"}`}>{day.dayType}</span></td>
                        <td><span className={`status ${day.status === "正常" ? "ok" : "warning"}`}>{day.status}</span></td>
                        <td>{formatTime(day.firstCheckIn)} — {formatTime(day.lastCheckOut)}</td>
                        <td>{formatMinutes(day.actualEffectiveMinutes)}</td>
                        <td>{formatMinutes(day.extraMinutes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <aside className="panel detail-panel">
              {selectedDay ? (
                <>
                  <div className="detail-title"><div><span>{selectedDay.workDate}</span><h3>星期{selectedDay.weekday} · {selectedDay.dayType}</h3></div><span className={`status ${selectedDay.status === "正常" ? "ok" : "warning"}`}>{selectedDay.status}</span></div>
                  <div className="calculation-list">
                    <div><span>应计标准工时</span><strong>{formatMinutes(selectedDay.requiredStandardMinutes)}</strong></div>
                    <div><span>原始跨度</span><strong>{formatMinutes(selectedDay.rawDurationMinutes)}</strong></div>
                    <div><span>午休扣除</span><strong>- {formatMinutes(selectedDay.lunchDeductionMinutes)}</strong></div>
                    <div><span>地点福利扣除</span><strong>- {formatMinutes(selectedDay.locationBenefitMinutes)}</strong></div>
                    <div className="total"><span>实际有效工时</span><strong>{formatMinutes(selectedDay.actualEffectiveMinutes)}</strong></div>
                    <div className="total extra"><span>加班/调休时长</span><strong>{formatMinutes(selectedDay.extraMinutes)}</strong></div>
                  </div>
                  <div className="notes"><h4>计算说明</h4>{selectedDay.messages.map((message) => <p key={message}>{message}</p>)}</div>
                  <div className="records"><h4>原始打卡</h4>{selectedDay.records.length ? selectedDay.records.map((record, index) => <div key={`${record.timestamp}-${index}`}><strong>{record.timestamp.slice(11, 16)} {record.punch_type}</strong><span>{record.location || "未记录地点"}</span></div>) : <p>当天没有打卡记录</p>}</div>
                </>
              ) : <p>请选择一条每日记录。</p>}
            </aside>
          </section>
        </>
      )}
    </main>
  );
}

export default App;
