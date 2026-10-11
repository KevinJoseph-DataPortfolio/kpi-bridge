import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, send } from "../api";
import { ErrorBox } from "../components/common";
import type { ColumnProfile, Plan } from "../types";

interface Props {
  projectId?: string;
  project?: { id: string };
  tableId: string;
  replacePlan: string | null;
  onDone: (planId: string) => void;
  onBack: () => void;
  embedded?: boolean;
}

interface AggSpec {
  fn: string;
  field: string | null;
  per: string | null;
}
interface MetricSpec {
  id: string;
  label: string;
  unit: string;
  kind: string;
  a: AggSpec;
  b: AggSpec | null;
  filters: never[];
  compare: string;
}
interface ChartSpec {
  id: string;
  type: string;
  metric: string;
  dimension: string;
  top_n: number | null;
}

const FNS = ["sum", "count_rows", "count", "count_distinct", "avg", "median", "min", "max"];
const KINDS = ["aggregate", "ratio", "difference", "difference_ratio", "percent_of"];
const UNITS = ["currency", "count", "number", "percent", "ratio"];
const NUMERIC = ["number", "money", "percent"];

function fieldsFor(fn: string, cols: ColumnProfile[]): ColumnProfile[] {
  if (fn === "count_rows") return [];
  if (["sum", "avg", "median"].includes(fn)) return cols.filter((c) => NUMERIC.includes(c.type));
  if (["min", "max"].includes(fn)) return cols.filter((c) => NUMERIC.includes(c.type) || c.type === "date");
  return cols;
}

export default function Builder({ projectId, project, tableId, replacePlan, onDone, onBack, embedded }: Props) {
  const { t, i18n } = useTranslation();
  const pid = projectId ?? project!.id;
  const [cols, setCols] = useState<ColumnProfile[]>([]);
  const [metrics, setMetrics] = useState<MetricSpec[]>([]);
  const [charts, setCharts] = useState<ChartSpec[]>([]);
  const [period, setPeriod] = useState<string>("");
  const [grain, setGrain] = useState("month");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    get<{ profile: { columns: ColumnProfile[] } }>(`/projects/${pid}/tables/${tableId}/config`)
      .then((c) => {
        setCols(c.profile.columns);
        const amount = c.profile.columns.find((x) => x.roles.includes("amount") && NUMERIC.includes(x.type)) ?? c.profile.columns.find((x) => NUMERIC.includes(x.type));
        setMetrics([
          {
            id: "m1",
            label: amount ? amount.name : t("metric_labels.row_count"),
            unit: amount ? (amount.type === "money" ? "currency" : "number") : "count",
            kind: "aggregate",
            a: amount ? { fn: "sum", field: amount.id, per: null } : { fn: "count_rows", field: null, per: null },
            b: null,
            filters: [],
            compare: "none",
          },
        ]);
        const date = c.profile.columns.find((x) => x.type === "date");
        if (date) setPeriod(date.id);
      })
      .catch(setError);
  }, [pid, tableId]); // eslint-disable-line react-hooks/exhaustive-deps

  const dims = cols.filter((c) => ["text", "id", "bool"].includes(c.type));

  function updateMetric(i: number, patch: Partial<MetricSpec>) {
    setMetrics(metrics.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  }

  async function submit() {
    setError(null);
    const spec = {
      metrics: metrics.map((m) => ({ ...m, b: m.kind === "aggregate" ? null : m.b, compare: period ? m.compare : "none" })),
      charts: charts.filter((c) => c.dimension !== "__period__" || period),
      period_field: period || null,
      time_grain: grain,
      filter_dimensions: dims.filter((d) => d.distinct > 1 && d.distinct <= 200).slice(0, 3).map((d) => d.id),
    };
    try {
      const plan = await send<Plan>("POST", `/projects/${pid}/builder`, { table_id: tableId, spec, ui_lang: i18n.language, replace_plan: replacePlan });
      onDone(plan.id);
    } catch (e) {
      setError(e);
    }
  }

  const AggEditor = ({ value, onChange, label }: { value: AggSpec; onChange: (a: AggSpec) => void; label: string }) => (
    <div className="agg">
      <span className="muted">{label}</span>
      <select value={value.fn} onChange={(e) => onChange({ ...value, fn: e.target.value, field: fieldsFor(e.target.value, cols)[0]?.id ?? null })} aria-label={t("builder.fn")}>
        {FNS.map((f) => (
          <option key={f} value={f}>
            {t(`builder.fns.${f}`)}
          </option>
        ))}
      </select>
      {value.fn !== "count_rows" && (
        <select value={value.field ?? ""} onChange={(e) => onChange({ ...value, field: e.target.value || null })} aria-label={t("builder.field")}>
          {fieldsFor(value.fn, cols).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      )}
      {["sum", "avg", "median"].includes(value.fn) && (
        <select value={value.per ?? ""} onChange={(e) => onChange({ ...value, per: e.target.value || null })} aria-label={t("builder.per")}>
          <option value="">{t("builder.per_line")}</option>
          {dims.map((c) => (
            <option key={c.id} value={c.id}>
              {t("builder.once_per", { name: c.name })}
            </option>
          ))}
        </select>
      )}
    </div>
  );

  return (
    <section className={embedded ? "builder embedded" : "screen builder"} data-testid="builder">
      <h2>{t("builder.title")}</h2>
      <p className="hint">{t("builder.hint")}</p>
      {metrics.map((m, i) => (
        <div className="metric-editor" key={i}>
          <div className="row gap">
            <input value={m.label} maxLength={80} onChange={(e) => updateMetric(i, { label: e.target.value })} aria-label={t("builder.label")} />
            <select value={m.kind} onChange={(e) => updateMetric(i, { kind: e.target.value, b: e.target.value === "aggregate" ? null : m.b ?? { fn: "count_distinct", field: cols[0]?.id ?? null, per: null } })} aria-label={t("builder.kind")}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {t(`builder.kinds.${k}`)}
                </option>
              ))}
            </select>
            <select value={m.unit} onChange={(e) => updateMetric(i, { unit: e.target.value })} aria-label={t("builder.unit")}>
              {UNITS.map((u) => (
                <option key={u} value={u}>
                  {t(`units.${u}`)}
                </option>
              ))}
            </select>
            {period && (
              <label className="check">
                <input type="checkbox" checked={m.compare === "previous_period"} onChange={(e) => updateMetric(i, { compare: e.target.checked ? "previous_period" : "none" })} />
                {t("builder.compare")}
              </label>
            )}
            <button className="btn btn-ghost btn-small" onClick={() => setMetrics(metrics.filter((_, k) => k !== i))} disabled={metrics.length === 1}>
              {t("common.delete")}
            </button>
          </div>
          <AggEditor value={m.a} onChange={(a) => updateMetric(i, { a })} label="A" />
          {m.kind !== "aggregate" && m.b && <AggEditor value={m.b} onChange={(b) => updateMetric(i, { b })} label="B" />}
        </div>
      ))}
      <button
        className="btn btn-ghost"
        disabled={metrics.length >= 8}
        onClick={() => setMetrics([...metrics, { id: `m${metrics.length + 1}_${Date.now() % 1000}`, label: t("builder.new_metric"), unit: "count", kind: "aggregate", a: { fn: "count_rows", field: null, per: null }, b: null, filters: [], compare: "none" }])}
      >
        + {t("builder.add_metric")}
      </button>
      <h3>{t("builder.charts")}</h3>
      {charts.map((c, i) => (
        <div className="row gap" key={i}>
          <select value={c.type} onChange={(e) => setCharts(charts.map((x, k) => (k === i ? { ...x, type: e.target.value } : x)))}>
            {["line", "bar", "hbar"].map((ty) => (
              <option key={ty} value={ty}>
                {t(`chart_types.${ty}`)}
              </option>
            ))}
          </select>
          <select value={c.metric} onChange={(e) => setCharts(charts.map((x, k) => (k === i ? { ...x, metric: e.target.value } : x)))}>
            {metrics.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          <select value={c.dimension} onChange={(e) => setCharts(charts.map((x, k) => (k === i ? { ...x, dimension: e.target.value, type: e.target.value === "__period__" ? "line" : x.type === "line" ? "bar" : x.type } : x)))}>
            {period && <option value="__period__">{t("builder.by_period")}</option>}
            {dims.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <input type="number" min={1} max={20} value={c.top_n ?? ""} placeholder="top N" onChange={(e) => setCharts(charts.map((x, k) => (k === i ? { ...x, top_n: e.target.value ? Number(e.target.value) : null } : x)))} />
          <button className="btn btn-ghost btn-small" onClick={() => setCharts(charts.filter((_, k) => k !== i))}>
            {t("common.delete")}
          </button>
        </div>
      ))}
      <button
        className="btn btn-ghost"
        disabled={charts.length >= 4 || !metrics.length}
        onClick={() => setCharts([...charts, { id: `ch${charts.length + 1}_${Date.now() % 1000}`, type: period ? "line" : "bar", metric: metrics[0].id, dimension: period ? "__period__" : dims[0]?.id ?? "__period__", top_n: null }])}
      >
        + {t("builder.add_chart")}
      </button>
      <div className="row gap">
        <label>
          {t("builder.period_field")}
          <select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="">{t("builder.no_period")}</option>
            {cols
              .filter((c) => c.type === "date")
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          {t("builder.grain")}
          <select value={grain} onChange={(e) => setGrain(e.target.value)}>
            {["day", "week", "month", "quarter", "year"].map((g) => (
              <option key={g} value={g}>
                {t(`grains.${g}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
      <div className="row gap">
        <button className="btn btn-primary" onClick={() => void submit()} data-testid="builder-submit">
          {t("builder.submit")}
        </button>
        {!embedded && (
          <button className="btn btn-ghost" onClick={onBack}>
            {t("common.back")}
          </button>
        )}
      </div>
    </section>
  );
}
