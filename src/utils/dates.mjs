export function argValue(argv, name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : null;
}

export function parseIsoDate(text) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text || "")) return null;
  const [year, month, day] = text.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.toISOString().slice(0, 10) === text ? date : null;
}

export function resolveDateRangeFromArgs(argv = process.argv) {
  const startText = argValue(argv, "--start-date");
  const endText = argValue(argv, "--end-date");
  const start = parseIsoDate(startText);
  const end = parseIsoDate(endText);
  if (!start || !end) throw new Error("Provide --start-date and --end-date as valid YYYY-MM-DD dates; the Python CLI resolves defaults.");
  if (start > end) throw new Error("--start-date must not be after --end-date.");
  return { start, end, startText, endText, days: (end - start) / 86400000 + 1, label: `${startText}_${endText}` };
}
