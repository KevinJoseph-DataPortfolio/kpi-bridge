import type { ExprJson, FilterJson } from "./types";

type T = (k: string, o?: Record<string, unknown>) => string;

export function describeExpr(e: ExprJson, names: Record<string, string>, t: T): string {
  if (e.kind === "const") return e.value;
  if (e.kind === "agg") {
    const field = e.field ? `« ${names[e.field] ?? e.field} »` : "";
    let s = t(`describe.fn.${e.fn}`, { field });
    if (e.per) s += " " + t("describe.per", { key: names[e.per] ?? e.per });
    if (e.filters?.length) s += " " + t("describe.where", { cond: e.filters.map((f) => describeFilter(f, names, t)).join(", ") });
    return s;
  }
  const sym = { add: "+", sub: "−", mul: "×", div: "÷" }[e.op] ?? e.op;
  return `(${describeExpr(e.left, names, t)} ${sym} ${describeExpr(e.right, names, t)})`;
}

export function describeFilter(f: FilterJson, names: Record<string, string>, t: T): string {
  return t(`describe.op.${f.op}`, { field: names[f.field] ?? f.field, values: f.values.join(", ") });
}
