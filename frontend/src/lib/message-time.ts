const SHOW_DELAY_MS = 400;
const HIDE_DELAY_MS = 180;

export function formatMessageTime(createdAt: number, now = new Date()): string {
  const date = new Date(createdAt * 1000);
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  const todaySerial = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const messageDaySerial = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDistance = Math.round((todaySerial - messageDaySerial) / 86400000);
  if (dayDistance === 0) return time;
  if (dayDistance === 1) return `昨天 ${time}`;
  if (dayDistance === 2) return `前天 ${time}`;
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
}

export { HIDE_DELAY_MS, SHOW_DELAY_MS };
