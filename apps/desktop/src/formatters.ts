export function formatMinutes(value: number | null): string {
  if (value === null) return "未计算";
  const sign = value < 0 ? "-" : "";
  const absolute = Math.abs(value);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  return sign + hours + "小时" + String(minutes).padStart(2, "0") + "分钟";
}

export function formatTime(value: string | null): string {
  if (!value) return "—";
  return value.slice(11, 16);
}
