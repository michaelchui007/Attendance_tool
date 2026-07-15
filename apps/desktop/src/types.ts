export interface AttendanceDay {
  workDate: string;
  weekday: string;
  dayType: string;
  status: string;
  requiredStandardMinutes: number;
  actualRegularMinutes: number | null;
  actualEffectiveMinutes: number | null;
  extraMinutes: number | null;
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
    normalDayCount: number;
    abnormalDayCount: number;
  };
  days: AttendanceDay[];
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
