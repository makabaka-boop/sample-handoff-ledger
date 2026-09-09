export interface ServerCountdown {
  remainingSeconds: (monotonicNow?: number) => number;
}
export function createServerCountdown(
  serverTime: string,
  expiresAt: string,
  monotonicStart = performance.now(),
): ServerCountdown {
  const serverStart = Date.parse(serverTime);
  const deadline = Date.parse(expiresAt);
  return {
    remainingSeconds(monotonicNow = performance.now()) {
      const estimatedServerNow = serverStart + Math.max(0, monotonicNow - monotonicStart);
      return Math.max(0, Math.ceil((deadline - estimatedServerNow) / 1000));
    },
  };
}

export function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  return hours > 0
    ? `${hours}时 ${minutes.toString().padStart(2, "0")}分`
    : `${minutes.toString().padStart(2, "0")}:${rest.toString().padStart(2, "0")}`;
}
