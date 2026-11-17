// Durations shown in seconds, minutes or hours, always stored as whole seconds.
// Used by Settings > Studio "Pause between requests" (stored as studio_pause_s).

export const UNITS = [
  { key: "hours", label: "hours", one: "hour", factor: 3600 },
  { key: "minutes", label: "minutes", one: "minute", factor: 60 },
  { key: "seconds", label: "seconds", one: "second", factor: 1 },
];

/** Saved seconds shown in the largest unit that divides them evenly: 3600 -> 1 hour, 90 -> 90 seconds. */
export function toDisplay(seconds) {
  const s = Math.round(Number(seconds));
  for (const u of UNITS) {
    if (s > 0 && s % u.factor === 0) return { value: s / u.factor, unit: u.key };
  }
  return { value: s, unit: "seconds" };
}

/** Typed value plus unit as whole seconds, or null when the value is empty or not a whole number. */
export function toSeconds(value, unit) {
  const text = String(value ?? "").trim();
  if (!/^\d+$/.test(text)) return null;
  const u = UNITS.find((x) => x.key === unit);
  if (!u) return null;
  return Number(text) * u.factor;
}

/** Plain wording for a number of seconds in its largest even unit: "45 seconds", "1 hour". */
export function describe(seconds) {
  const { value, unit } = toDisplay(seconds);
  const u = UNITS.find((x) => x.key === unit);
  return `${value} ${value === 1 ? u.one : u.label}`;
}

/** Check a typed pause; returns { seconds } or { error } with a plain message (never clamps). */
export function checkPause(value, unit, min, max) {
  const seconds = toSeconds(value, unit);
  const range = `from ${describe(min)} to ${describe(max)}`;
  if (seconds === null) return { error: `Enter a whole number for the pause, ${range}.` };
  if (seconds < min || seconds > max) return { error: `The pause must be ${range}.` };
  return { seconds };
}
