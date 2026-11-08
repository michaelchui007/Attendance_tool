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
}

/** 本次文件会话内的每日首尾覆盖值，null 明确表示缺卡。 */
export interface DayOverride {
  workDate: string;
  firstCheckIn: string | null;
  lastCheckOut: string | null;
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
  };
  days: AttendanceDay[];
  compensatoryLeaves: LeaveRecord[];
  rules: {
    lunchStart: string;
    lunchEnd: string;
    workdayStart: string;
    workdayEnd: string;
    dayBoundary: string;
    standardMinutes: number;
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
  outputPath: string;
}

export interface NativePathSelection {
  path: string | null;
  pathUtf16: number[] | null;
}
