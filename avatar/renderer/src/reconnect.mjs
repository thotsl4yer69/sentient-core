/** Delay before the next broker or stream attempt. Always under 10 seconds. */
export function reconnectDelayMs(attempt) {
  const n = Math.max(0, attempt | 0);
  return Math.min(8000, 500 * 2 ** Math.min(n, 4));
}
