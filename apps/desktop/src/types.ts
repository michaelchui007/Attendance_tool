/** 源申请保留原值；每日申请量与实际扣减分别由引擎返回。 */
export interface LeaveRecord {
  workDate: string;
  minutes: number;
  description: string;
  sourceSheet: string;
  sourceRow: number;
  startAt: string | null;
  endAt: string | null;
  parseWarning: string;
  sourceFile?: string;
  sourceId?: string;
  sources?: RecordSource[];
}
export interface RecordSource { sourceId: string; path: string; name: string; sourceSheet: string; sourceRow: number; }

/** 正常时段与标准工时相互独立；null 午休表示无午休。 */
export interface Schedule { workdayStart: string; workdayEnd: string; lunchStart: string | null; lunchEnd: string | null; }
export interface DateRule extends Schedule { id: string; name: string; startDate: string; endDate: string; }
export interface CalculationSettings extends Schedule { standardMinutes: number; dateRules: DateRule[]; }
export interface AppSettings { version: 1; calculation: CalculationSettings; outputRoot: string; }
export interface SourceFile { id: string; path: string; name: string; startDate: string; endDate: string; duplicateOf?: string | null; cacheHit?: boolean; }
export interface SourceConflict { workDate: string; sourceIds: string[]; reason: string; resolved: boolean; selectedSourceId?: string | null; }
export interface ProgressInfo { stage: string; message: string; current?: number; total?: number; }

/** 本次文件会话内的每日首尾覆盖值，null 明确表示缺卡。 */
export interface DayOverride {
  workDate: string;
  firstCheckIn?: string | null;
  lastCheckOut?: string | null;
  compensatoryLeaveMinutes?: number | null;
  leaveReason?: string;
  schedule?: Schedule | null;
}

export interface AttendanceDay {
  workDate: string;
  weekday: string;
  dayType: string;
  status: string;
  requiredStandardMinutes: number;
  actualRegularMinutes: number | null;
  actualEffectiveMinutes: number | null;
  extraMinutes: number | null;
  compensatoryLeaveMinutes: number | null;
  netExtraMinutes: number | null;
  leaveRequestedMinutes: number | null;
  leaveRecords: LeaveRecord[];
  originalFirstCheckIn: string | null;
  originalLastCheckOut: string | null;
  isEdited: boolean;
  requiresReview: boolean;
  covered: boolean;
  sourceIds: string[];
  schedule: Schedule & { standardMinutes: number; netScheduleMinutes: number };
  scheduleSource: string;
  autoCompensatoryLeaveMinutes: number | null;
  isLeaveEdited: boolean;
  leaveReason: string;
  rawDurationMinutes: number | null;
  lunchDeductionMinutes: number;
  locationBenefitMinutes: number;
  firstCheckIn: string | null;
  lastCheckOut: string | null;
  messages: string[];
  records: Array<{
    timestamp: string;
    punch_type: string;
    location: string;
    status: string;
    source_file?: string;
    source_sheet?: string;
    source_row?: number;
    sources?: RecordSource[];
  }>;
}

export interface AttendanceResult {
  success: boolean;
  employee: { name: string; account: string };
  period: { startDate: string; endDate: string };
  summary: {
    requiredStandardMinutes: number;
    actualEffectiveMinutes: number;
    extraMinutes: number;
    compensatoryLeaveMinutes: number | null;
    netExtraMinutes: number | null;
    leaveRequestedMinutes: number | null;
    requiresReview: boolean;
    normalDayCount: number;
    abnormalDayCount: number;
    isPartial?: boolean;
  };
  days: AttendanceDay[];
  compensatoryLeaves: LeaveRecord[];
  rules: CalculationSettings & {
    dayBoundary: string;
    locationBenefitEnabled: false;
  };
  calendar: {
    strategy: string;
    years: Array<{
      year: number;
      source: "online" | "cache" | "stale-cache" | "builtin";
      fetchedAt?: string;
      sourceUrl?: string;
    }>;
    warnings: string[];
  };
  outputPath: string | null;
  exportBlocked?: boolean;
  sources: SourceFile[];
  conflicts: SourceConflict[];
  coverage: { missingDates: string[]; coveredDayCount: number };
  monthlySummaries: Array<AttendanceResult["summary"] & { month: string }>;
  timings?: Record<string, number>;
}

export interface NativePathSelection {
  path: string | null;
  pathUtf16: number[] | null;
}
