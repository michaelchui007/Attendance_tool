import { useState } from "react";

type HelpSection = "quick" | "results" | "rules" | "faq";

interface HelpDrawerProps {
  open: boolean;
  version: string;
  onClose: () => void;
}

const sectionMeta: Array<{ key: HelpSection; index: string; title: string; summary: string }> = [
  { key: "quick", index: "01", title: "快速开始", summary: "选择文件、统计并查看结果" },
  { key: "results", index: "02", title: "结果怎么看", summary: "了解六项汇总数据" },
  { key: "rules", index: "03", title: "计算规则", summary: "工作日、午休、加班与调休" },
  { key: "faq", index: "04", title: "常见问题", summary: "快速排查容易疑惑的结果" },
];

function QuickHelp() {
  return (
    <div className="help-section-body">
      <div className="help-step-flow" aria-label="使用步骤">
        {[
          ["1", "选择 Excel"],
          ["2", "开始统计"],
          ["3", "查看结果"],
          ["4", "打开导出"],
        ].map(([index, label], position) => (
          <div className="help-step-wrap" key={index}>
            <div className="help-step"><strong>{index}</strong><span>{label}</span></div>
            {position < 3 && <span className="help-arrow">→</span>}
          </div>
        ))}
      </div>
      <ul className="help-list">
        <li>选择员工个人导出的 <strong>.xlsx</strong> 考勤报表。</li>
        <li>输出目录可不选，默认在源文件旁创建 <strong>export_report</strong>。</li>
        <li>点击“开始统计”，完成后可查看汇总、每日明细和导出 Excel。</li>
        <li>考勤文件仅在本机处理；联网仅用于更新节假日安排。</li>
      </ul>
    </div>
  );
}

function ResultHelp() {
  const items = [
    ["standard", "应计标准工时", "工作日每天 8 小时，周末和法定节假日为 0。"],
    ["effective", "实际有效工时", "完整上下班跨度扣除工作日午休后的时间。"],
    ["overtime", "加班时长", "工作日超过 8 小时、地点福利以及休息日打卡的合计。"],
    ["leave", "调休时长", "从报表中的“调休假 N 小时”自动读取。"],
    ["net", "净剩加班", "加班时长减去调休时长，结果可以为负数。"],
    ["warning", "异常日期", "缺少打卡或上下班时间不合理的日期。"],
  ];
  return (
    <div className="help-section-body help-metric-list">
      {items.map(([tone, title, description]) => (
        <div className="help-metric" key={title}>
          <span className={"help-dot " + tone} />
          <div><strong>{title}</strong><p>{description}</p></div>
        </div>
      ))}
    </div>
  );
}

function RuleHelp() {
  return (
    <div className="help-section-body">
      <div className="help-rule-card featured">
        <span className="help-rule-label">工作日</span>
        <div className="help-formula-flow">
          <span>上下班跨度</span><i>−</i><span>午休重叠</span><i>=</i><strong>实际有效</strong>
        </div>
        <div className="help-formula-flow compact">
          <span>实际有效</span><i>+</i><span>地点福利</span><i>−</i><span>8小时</span><i>=</i><strong>加班</strong>
        </div>
      </div>
      <div className="help-rule-grid">
        <div className="help-rule-card"><strong>午休</strong><p>工作日扣除与 12:00–13:30 实际重叠的时间。</p></div>
        <div className="help-rule-card"><strong>地点福利</strong><p>地点包含“云岭东路586号”，工作日加班折算 30 分钟。</p></div>
        <div className="help-rule-card"><strong>周末和节假日</strong><p>不扣午休、不享受地点福利，打卡跨度全部计入加班。</p></div>
        <div className="help-rule-card"><strong>多次打卡</strong><p>取最早上班和最晚下班；03:00 前的打卡归入前一天。</p></div>
      </div>
      <div className="help-net-formula">
        <span>加班时长</span><b>−</b><span>调休时长</span><b>=</b><strong>净剩加班</strong>
      </div>
      <p className="help-callout">缺卡工作日仍计入 8 小时标准工时，但实际有效和加班显示“未计算”；当天调休仍会参与净剩加班。</p>
    </div>
  );
}

function FaqHelp() {
  return (
    <div className="help-section-body help-faq">
      <details>
        <summary>为什么工作满 8 小时仍有 30 分钟加班？</summary>
        <p>当天打卡地点命中“云岭东路586号”时，会获得 30 分钟加班折算，但不会增加实际有效工时。</p>
      </details>
      <details>
        <summary>为什么缺卡当天仍有 8 小时标准工时？</summary>
        <p>标准工时由日期类型决定。只要当天是工作日，就会计入 8 小时，与是否打卡无关。</p>
      </details>
      <details>
        <summary>为什么净剩加班会是负数？</summary>
        <p>说明调休时长大于当前累计加班。例如加班 2 小时、调休 8 小时，净剩加班为 -6 小时。</p>
      </details>
      <details>
        <summary>普通周末为什么不扣午休？</summary>
        <p>当前规则将休息日的完整打卡跨度计入加班，因此不扣除午休。</p>
      </details>
      <details>
        <summary>考勤文件会上传吗？</summary>
        <p>不会。考勤读取、计算和导出均在本机完成；软件联网仅用于更新节假日安排。</p>
      </details>
    </div>
  );
}

export default function HelpDrawer({ open, version, onClose }: HelpDrawerProps) {
  const [activeSection, setActiveSection] = useState<HelpSection>("quick");
  if (!open) return null;

  return (
    <div className="drawer-overlay" role="presentation" onMouseDown={onClose}>
      <aside className="drawer help-drawer" role="dialog" aria-modal="true" aria-labelledby="help-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="drawer-header">
          <div><p className="eyebrow">USER GUIDE</p><h2 id="help-title">考勤统计帮助</h2><span>轻松了解使用方法和计算规则</span></div>
          <button className="drawer-close" onClick={onClose} aria-label="关闭帮助">×</button>
        </header>
        <div className="drawer-scroll">
          <div className="help-accordion">
            {sectionMeta.map((section) => {
              const active = section.key === activeSection;
              return (
                <section className={"help-section " + (active ? "active" : "")} key={section.key}>
                  <button className="help-section-toggle" onClick={() => setActiveSection(section.key)} aria-expanded={active}>
                    <span>{section.index}</span>
                    <div><strong>{section.title}</strong><small>{section.summary}</small></div>
                    <i>{active ? "−" : "+"}</i>
                  </button>
                  <div className="help-section-content">
                    {active && section.key === "quick" && <QuickHelp />}
                    {active && section.key === "results" && <ResultHelp />}
                    {active && section.key === "rules" && <RuleHelp />}
                    {active && section.key === "faq" && <FaqHelp />}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
        <footer className="drawer-footer"><span>考勤统计</span><strong>v{version}</strong></footer>
      </aside>
    </div>
  );
}
