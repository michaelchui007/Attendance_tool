import type { AttendanceResult } from "../types";
import { formatMinutes } from "../formatters";
import { needsAttention } from "../attendanceState";

export type InsightKey = "required" | "effective" | "overtime" | "leave" | "net" | "abnormal" | "calendar";

interface InsightDrawerProps {
  active: InsightKey | null;
  result: AttendanceResult;
  onClose: () => void;
  onSelectDay: (workDate: string) => void;
  onShowAbnormal: () => void;
}

function BreakdownRow({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="breakdown-row">
      <div><strong>{label}</strong>{note && <small>{note}</small>}</div>
      <span>{value}</span>
    </div>
  );
}

function DateButton({ date, title, value, tone, onClick }: {
  date: string;
  title: string;
  value: string;
  tone?: "positive" | "negative" | "warning";
  onClick: () => void;
}) {
  return (
    <button className="insight-date-row" onClick={onClick}>
      <span className="insight-date">{date.slice(5)}</span>
      <span className="insight-date-title">{title}</span>
      <strong className={tone || ""}>{value}</strong>
      <i>›</i>
    </button>
  );
}

function sourceLabel(source: AttendanceResult["calendar"]["years"][number]["source"]): string {
  return {
    online: "在线更新",
    cache: "本地缓存",
    "stale-cache": "离线缓存",
    builtin: "内置数据",
  }[source];
}

export default function InsightDrawer({
  active,
  result,
  onClose,
  onSelectDay,
  onShowAbnormal,
}: InsightDrawerProps) {
  if (!active) return null;

  const workdays = result.days.filter((day) => day.requiredStandardMinutes > 0);
  const completeDays = result.days.filter((day) => day.actualEffectiveMinutes !== null);
  const rawTotal = completeDays.reduce((sum, day) => sum + (day.rawDurationMinutes || 0), 0);
  const lunchTotal = completeDays.reduce((sum, day) => sum + day.lunchDeductionMinutes, 0);
  const baseWorkdayOvertime = result.days.reduce((sum, day) => {
    if (day.requiredStandardMinutes === 0 || day.actualEffectiveMinutes === null) return sum;
    return sum + Math.max(0, day.actualEffectiveMinutes - day.requiredStandardMinutes);
  }, 0);
  const restOvertime = result.days.reduce((sum, day) => {
    if (day.requiredStandardMinutes > 0) return sum;
    return sum + (day.extraMinutes || 0);
  }, 0);
  const abnormalDays = result.days.filter(needsAttention);

  const requiredGroups = Array.from(
    workdays.reduce((groups, day) => {
      const current = groups.get(day.dayType) || { count: 0, minutes: 0 };
      current.count += 1;
      current.minutes += day.requiredStandardMinutes;
      groups.set(day.dayType, current);
      return groups;
    }, new Map<string, { count: number; minutes: number }>()),
  );

  const abnormalGroups = Array.from(
    abnormalDays.reduce((groups, day) => {
      const status = day.requiresReview ? "调休待核对" : day.status;
      groups.set(status, (groups.get(status) || 0) + 1);
      return groups;
    }, new Map<string, number>()),
  );

  // 未知扣减会使后续累计余额未知，不能按零继续累计。
  let runningBalance: number | null = 0;
  const balanceRows = result.days.filter(day => day.covered !== false).map((day) => {
    const delta = day.netExtraMinutes;
    runningBalance = runningBalance === null || delta === null ? null : runningBalance + delta;
    return { day, delta, balance: runningBalance };
  }).filter((item) => item.delta !== 0);

  const titles: Record<InsightKey, { eyebrow: string; title: string; value: string }> = {
    required: { eyebrow: "STANDARD HOURS", title: "应计标准工时", value: formatMinutes(result.summary.requiredStandardMinutes) },
    effective: { eyebrow: "EFFECTIVE HOURS", title: "实际有效工时", value: formatMinutes(result.summary.actualEffectiveMinutes) },
    overtime: { eyebrow: "OVERTIME", title: "加班时长", value: formatMinutes(result.summary.extraMinutes) },
    leave: { eyebrow: "COMPENSATORY LEAVE", title: "实际调休扣减", value: result.summary.compensatoryLeaveMinutes === null ? "待核对" : formatMinutes(result.summary.compensatoryLeaveMinutes) },
    net: { eyebrow: "NET OVERTIME", title: "净剩加班时长", value: result.summary.netExtraMinutes === null ? "待核对" : formatMinutes(result.summary.netExtraMinutes) },
    abnormal: { eyebrow: "ATTENDANCE ALERTS", title: "待处理日期", value: abnormalDays.length + " 天" },
    calendar: { eyebrow: "HOLIDAY CALENDAR", title: "节假日数据", value: (result.calendar.years || []).length + " 个年份" },
  };
  const heading = titles[active];

  return (
    <div className="drawer-overlay" role="presentation" onMouseDown={onClose}>
      <aside className="drawer insight-drawer" role="dialog" aria-modal="true" aria-labelledby="insight-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="drawer-header">
          <div><p className="eyebrow">{heading.eyebrow}</p><h2 id="insight-title">{heading.title}</h2><span>点击日期可返回每日计算详情</span></div>
          <button className="drawer-close" onClick={onClose} aria-label="关闭详情">×</button>
        </header>
        <div className="drawer-scroll">
          <div className={"insight-hero " + active}><span>当前结果</span><strong>{heading.value}</strong></div>

          {active === "required" && (
            <section className="insight-section">
              <h3>工作日构成</h3>
              <p>工作日采用本次配置的标准工时，缺卡不会减少标准；未导入日期不纳入合计。</p>
              <div className="breakdown-list">
                {requiredGroups.map(([dayType, group]) => (
                  <BreakdownRow key={dayType} label={dayType} note={group.count + " 天，按本次标准工时累计"} value={formatMinutes(group.minutes)} />
                ))}
                <BreakdownRow label="休息日" note={(result.days.filter(day => day.covered !== false).length - workdays.length) + " 天 × 0 小时"} value="0小时00分钟" />
              </div>
            </section>
          )}

          {active === "effective" && (
            <>
              <section className="insight-section">
                <h3>计算过程</h3>
                <div className="formula-bridge">
                  <div><span>完整打卡跨度</span><strong>{formatMinutes(rawTotal)}</strong></div>
                  <b>−</b>
                  <div><span>午休扣除</span><strong>{formatMinutes(lunchTotal)}</strong></div>
                  <b>=</b>
                  <div className="highlight"><span>实际有效</span><strong>{formatMinutes(result.summary.actualEffectiveMinutes)}</strong></div>
                </div>
              </section>
              <section className="insight-section">
                <h3>参与汇总</h3>
                <div className="breakdown-list">
                  <BreakdownRow label="已计算日期" value={completeDays.length + " 天"} />
                  <BreakdownRow label="未计算日期" note="缺卡或没有完整上下班" value={(result.days.length - completeDays.length) + " 天"} />
                </div>
              </section>
            </>
          )}

          {active === "overtime" && (
            <section className="insight-section">
              <h3>加班来源</h3>
              <p>工作日实际有效工时超过所配标准工时的部分，加上休息日完整打卡跨度。地点不再影响结果。</p>
              <div className="breakdown-list">
                <BreakdownRow label="工作日超时" value={formatMinutes(baseWorkdayOvertime)} />
                <BreakdownRow label="休息日打卡" value={formatMinutes(restOvertime)} />
                <BreakdownRow label="合计" value={formatMinutes(result.summary.extraMinutes)} />
              </div>
            </section>
          )}

          {active === "leave" && (
            <section className="insight-section">
              <h3>已识别的调休申请</h3>
              <p>以下保留源表申请原值。每日申请量按工作时段分摊，实际扣减另按剩余标准工时限制；点击查看当天明细。</p>
              <BreakdownRow label="实际扣减合计" value={result.summary.compensatoryLeaveMinutes === null ? "待核对" : formatMinutes(result.summary.compensatoryLeaveMinutes)} />
              {result.compensatoryLeaves.length ? (
                <div className="leave-record-list">
                  {result.compensatoryLeaves.map((record, index) => (
                    <button key={record.workDate + record.description + index} className="leave-record" onClick={() => onSelectDay(record.workDate)}>
                      <div><span>{record.workDate}</span><strong>{formatMinutes(record.minutes)}</strong></div>
                      <p>{record.description}</p>
                      <small>来自 {record.sourceSheet} · 第 {record.sourceRow} 行</small>
                    </button>
                  ))}
                </div>
              ) : <div className="insight-empty">当前报表没有识别到调休申请</div>}
            </section>
          )}

          {active === "net" && (
            <>
              <section className="insight-section">
                <h3>余额计算</h3>
                <div className="net-equation">
                  <div><span>加班</span><strong>{formatMinutes(result.summary.extraMinutes)}</strong></div>
                  <b>−</b>
                  <div><span>实际扣减</span><strong>{result.summary.compensatoryLeaveMinutes === null ? "待核对" : formatMinutes(result.summary.compensatoryLeaveMinutes)}</strong></div>
                  <b>=</b>
                  <div className="highlight"><span>净剩</span><strong>{result.summary.netExtraMinutes === null ? "待核对" : formatMinutes(result.summary.netExtraMinutes)}</strong></div>
                </div>
              </section>
              <section className="insight-section">
                <h3>已覆盖日期余额变化</h3>{result.summary.isPartial && <p>存在未导入区间，以下余额仅代表已覆盖日期。</p>}
                <div className="balance-list">
                  {balanceRows.map(({ day, delta, balance }) => (
                    <DateButton
                      key={day.workDate}
                      date={day.workDate}
                      title={(delta === null ? "当日扣减待核对" : (delta >= 0 ? "增加 " : "减少 ") + formatMinutes(Math.abs(delta))) + " · 余额 " + (balance === null ? "待核对" : formatMinutes(balance))}
                      value={delta === null ? "待核对" : (delta >= 0 ? "+" : "-") + formatMinutes(Math.abs(delta))}
                      tone={delta === null ? "warning" : delta >= 0 ? "positive" : "negative"}
                      onClick={() => onSelectDay(day.workDate)}
                    />
                  ))}
                </div>
              </section>
            </>
          )}

          {active === "abnormal" && (
            <>
              <section className="insight-section">
                <h3>异常类型</h3>
                <div className="breakdown-list">
                  {abnormalGroups.map(([status, count]) => <BreakdownRow key={status} label={status} value={count + " 天"} />)}
                </div>
                <button className="insight-action" onClick={onShowAbnormal}>在每日明细中查看全部异常</button>
              </section>
              <section className="insight-section">
                <h3>异常日期</h3>
                <div className="balance-list">
                  {abnormalDays.map((day) => (
                    <DateButton key={day.workDate} date={day.workDate} title={day.dayType} value={day.requiresReview ? "调休待核对" : day.status} tone="warning" onClick={() => onSelectDay(day.workDate)} />
                  ))}
                </div>
              </section>
            </>
          )}

          {active === "calendar" && (
            <section className="insight-section">
              <h3>当前数据来源</h3>
              <p>软件可能联网更新节假日安排，但不会上传考勤文件。</p>
              <div className="calendar-source-list">
                {(result.calendar.years || []).map((item) => (
                  <div className="calendar-source-item" key={item.year}>
                    <span>{item.year}</span>
                    <div><strong>{sourceLabel(item.source)}</strong>{item.fetchedAt && <small>更新时间：{new Date(item.fetchedAt).toLocaleString("zh-CN")}</small>}</div>
                  </div>
                ))}
              </div>
              {(result.calendar.warnings || []).length > 0 && (
                <div className="calendar-warnings">{(result.calendar.warnings || []).map((warning) => <p key={warning}>{warning}</p>)}</div>
              )}
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}
