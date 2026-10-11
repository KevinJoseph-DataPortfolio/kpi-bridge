// Affichage localisé. Les valeurs arrivent comme chaînes décimales exactes ; Intl.NumberFormat accepte les chaînes
// (pas de conversion intermédiaire en flottant) et arrondit uniquement pour l'affichage.

const DEFAULT_DECIMALS: Record<string, number> = { currency: 2, percent: 1, count: 0, ratio: 2, number: 2 };

function locale(lang: string): string {
  return lang === "zh-Hans" ? "zh-CN" : lang;
}

export function formatValue(
  value: string | null | undefined,
  lang: string,
  unit: string = "number",
  currency?: string | null,
  decimals?: number | null,
): string | null {
  if (value === null || value === undefined) return null;
  let places = decimals ?? DEFAULT_DECIMALS[unit] ?? 2;
  if (unit === "number" && decimals == null && !value.includes(".")) places = 0;
  const opts: Intl.NumberFormatOptions = { minimumFractionDigits: places, maximumFractionDigits: places };
  if (unit === "currency" && currency && /^[A-Z]{3}$/.test(currency)) {
    opts.style = "currency";
    opts.currency = currency;
  }
  const nf = new Intl.NumberFormat(locale(lang), opts);
  // format(string) : précision décimale préservée (Intl.NumberFormat v3)
  const formatted = nf.format(value as unknown as number);
  return unit === "percent" ? `${formatted} %` : formatted;
}

export function formatDate(iso: string | null | undefined, lang: string, grain?: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, (m || 1) - 1, d || 1));
  if (grain === "month") return new Intl.DateTimeFormat(locale(lang), { year: "numeric", month: "short", timeZone: "UTC" }).format(date);
  if (grain === "year") return String(y);
  if (grain === "quarter") return `${y} T${Math.floor(((m || 1) - 1) / 3) + 1}`;
  return new Intl.DateTimeFormat(locale(lang), { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC" }).format(date);
}

export function formatCell(v: unknown, type: string | undefined, lang: string): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "✓" : "✗";
  const s = String(v);
  if (type === "number" || type === "money" || type === "percent") {
    const places = s.includes(".") ? Math.min(6, s.split(".")[1].length) : 0;
    return new Intl.NumberFormat(locale(lang), { minimumFractionDigits: places, maximumFractionDigits: places }).format(s as unknown as number);
  }
  if (type === "date") return formatDate(s, lang);
  return s;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}
