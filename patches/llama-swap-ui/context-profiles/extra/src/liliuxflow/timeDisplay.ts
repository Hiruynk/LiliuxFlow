import { formatAbsoluteTime, formatRelativeTime, formatUptime } from "../lib/format";

/** Human display only. The existing Date parser, timestamp and uptime source
 * stay untouched; English delegates to its original exact formatter. */
export function localizedRelativeTime(timestamp: string, locale: string, nowMs = Date.now()): string {
  if (locale === "en") return formatRelativeTime(timestamp);
  const date = new Date(timestamp);
  const seconds = Math.floor((nowMs - date.getTime()) / 1000);
  if (!Number.isFinite(seconds)) return formatRelativeTime(timestamp);
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "always", style: "short" });
  if (seconds < 5) return new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" }).format(0, "second");
  if (seconds < 60) return formatter.format(-seconds, "second");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return formatter.format(-minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return formatter.format(-hours, "hour");
  return formatAbsoluteTime(timestamp);
}

export function localizedUptime(ms: number, locale: string): string {
  if (locale === "en" || !Number.isFinite(ms)) return formatUptime(ms);
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60), hours = Math.floor(minutes / 60);
  const unit = (value: number, name: string) => new Intl.NumberFormat(locale, { style: "unit", unit: name, unitDisplay: "short" }).format(value);
  if (seconds < 60) return unit(seconds, "second");
  if (minutes < 60) return `${unit(minutes, "minute")} ${unit(seconds % 60, "second")}`;
  if (hours < 24) return `${unit(hours, "hour")} ${unit(minutes % 60, "minute")}`;
  return `${unit(Math.floor(hours / 24), "day")} ${unit(hours % 24, "hour")}`;
}
