import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, download, saveBlob, send } from "../api";
import Chart from "../components/Chart";
import { ErrorBox, Modal } from "../components/common";
import { describeExpr, describeFilter } from "../describe";
import { formatCell, formatDate, formatValue } from "../format";
import type { Card, Dashboard, Detail, Filters, Project } from "../types";

interface Props {
  project: Project;
  planId: string;
  onModify: () => void;
  onInvalid: () => void;
  onDeleted: () => void;
}

export default function DashboardView({ project, planId, onModify, onInvalid, onDeleted }: Props) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const [data, setData] = useState<Dashboard | null>(null);
  const [filters, setFilters] = useState<Filters>((project.state.last_filters as Filters) || {});
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadMs, setLoadMs] = useState<number | null>(null);
  const base = `/projects/${project.id}/plans/${planId}`;

  const load = useCallback(
    async (f: Filters) => {
      setBusy(true);
      const t0 = performance.now();
      try {
        const d = await send<Dashboard>("POST", `${base}/dashboard`, { filters: f });
        setData(d);
        setDetail(d.detail);
        setLoadMs(Math.round(performance.now() - t0));
      } catch (e) {
        if (e instanceof ApiError && (e.code === "plan_invalidated_by_new_version" || e.code === "plan_not_validated")) onInvalid();
        setError(e);
      } finally {
        setBusy(false);
      }
    },
    [base, onInvalid],
  );

  useEffect(() => {
    void load(filters);
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  function apply(next: Filters) {
    setFilters(next);
    void load(next);
  }

  async function exportAs(format: "pdf" | "png" | "csv") {
    setError(null);
    try {
      const { blob, filename } = await download(`${base}/export`, { format, filters, lang });
      saveBlob(blob, filename);
    } catch (e) {
      setError(e);
    }
  }

  async function saveRecipe() {
    const name = window.prompt(t("recipe.name_prompt"), project.name);
    if (!name) return;
    try {
      const r = await send<{ id: string }>("POST", `/projects/${project.id}/recipes`, { plan_id: planId, name });
      const res = await fetch(`/api/v1/recipes/${r.id}`, { credentials: "same-origin" });
      saveBlob(await res.blob(), `${name.replace(/[^\w-]+/g, "_")}.kpirecipe.json`);
    } catch (e) {
      setError(e);
    }
  }

  if (!data) return <div className="muted">{busy ? t("common.loading") : ""}<ErrorBox error={error} /></div>;
  const grain = data.plan.time_grain;
  const names = Object.fromEntries((project.tables.find((x) => x.id === (data as unknown as { table_id: string }).table_id)?.columns || []).map((c) => [c.id, c.name]));

  return (
    <section className="screen dashboard" data-testid="dashboard">
      <div className="dash-head">
        <h1>{project.name}</h1>
        <div className="row gap wrap">
          <button className="btn btn-ghost" onClick={onModify} data-testid="modify-request">
            {t("dashboard.modify")}
          </button>
          <button className="btn btn-ghost" onClick={() => void exportAs("pdf")} data-testid="export-pdf">
            PDF
          </button>
          <button className="btn btn-ghost" onClick={() => void exportAs("png")} data-testid="export-png">
            PNG
          </button>
          <button className="btn btn-ghost" onClick={() => void exportAs("csv")} data-testid="export-csv">
            CSV
          </button>
          <button className="btn btn-ghost" onClick={() => void saveRecipe()} data-testid="save-recipe">
            {t("dashboard.save_recipe")}
          </button>
          <button
            className="btn btn-ghost danger"
            onClick={() => {
              if (window.confirm(t("projects.confirm_delete", { name: project.name }))) onDeleted();
            }}
            data-testid="delete-project"
          >
            {t("dashboard.delete_project")}
          </button>
        </div>
      </div>
      <p className="muted small">{t("dashboard.saved_hint")}</p>
      <FilterBar data={data} filters={filters} onApply={apply} />
      <p className="muted small" data-testid="scope">
        {t("dashboard.scope", { rows: data.rows_in_scope })}
        {data.coverage ? ` · ${t("dashboard.coverage", { from: formatDate(data.coverage[0], lang), to: formatDate(data.coverage[1], lang) })}` : ""}
        {data.global_filters.length > 0 && ` · ${t("dashboard.global_filters")} ${data.global_filters.map((f) => describeFilter(f, names, t)).join(", ")}`}
        {loadMs !== null && <span className="sr-only" data-testid="load-ms">{loadMs}</span>}
      </p>
      <ErrorBox error={error} onClose={() => setError(null)} />
      <div className={`cards ${busy ? "busy" : ""}`}>
        {data.cards.map((c) => (
          <KpiCard key={c.id} card={c} onVerify={() => setEvidence(c.id)} />
        ))}
      </div>
      <div className="charts">
        {data.charts.map((ch) => (
          <div className="chart-box" key={ch.id} data-testid={`chart-${ch.id}`}>
            <h3>{ch.title}</h3>
            <Chart data={ch} grain={grain} />
          </div>
        ))}
      </div>
      <QualityPanel data={data} />
      {detail && <DetailTable detail={detail} onPage={(off) => send<Detail>("POST", `${base}/detail`, { filters, offset: off }).then(setDetail).catch(setError)} />}
      {evidence && <EvidenceModal base={base} metricId={evidence} filters={filters} names={names} onClose={() => setEvidence(null)} />}
    </section>
  );
}

function KpiCard({ card, onVerify }: { card: Card; onVerify: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const ok = card.status === "ok";
  const v = card.variation;
  return (
    <div className={`card ${ok ? "" : "unavailable"}`} data-testid={`card-${card.id}`}>
      <div className="card-label">{card.label}</div>
      {ok ? (
        card.values.map((val, i) => (
          <div className={card.values.length > 1 ? "card-value multi" : "card-value"} key={i} data-testid="card-value">
            {val.value === null ? (
              <span className="muted" title={t(`reasons.${val.reason ?? "no_values"}`)}>
                {t("dashboard.unavailable")}
              </span>
            ) : (
              formatValue(val.value, lang, card.unit, val.currency, card.decimals)
            )}
            {card.values.length > 1 && <span className="cur">{val.currency ?? "—"}</span>}
          </div>
        ))
      ) : (
        <div className="card-value muted" data-testid="card-value">
          {t("dashboard.unavailable")}
          <div className="reason">{t(`reasons.${card.reason ?? "no_values"}`)}</div>
        </div>
      )}
      {card.currency_split && <div className="muted small">{t("dashboard.currency_split")}</div>}
      {v && (
        <div className="variation small">
          {v.status === "ok" && v.values?.[0]?.rate != null ? (
            <span className={Number(v.values[0].rate) >= 0 ? "up" : "down"} title={t(`variation.${v.rule}`, { cur: v.current?.join(" → "), prev: v.previous?.join(" → ") })}>
              {Number(v.values[0].rate) >= 0 ? "▲" : "▼"} {formatValue(v.values[0].rate, lang, "percent")} <span className="muted">{t(`variation.short.${v.rule}`)}</span>
            </span>
          ) : (
            <span className="muted">{t("variation.unavailable", { reason: t(`reasons.${v.reason ?? v.values?.[0]?.reason ?? "no_comparable_period"}`) })}</span>
          )}
        </div>
      )}
      <button className="link small verify" onClick={onVerify} data-testid={`verify-${card.id}`}>
        {t("dashboard.verify")}
      </button>
    </div>
  );
}

function FilterBar({ data, filters, onApply }: { data: Dashboard; filters: Filters; onApply: (f: Filters) => void }) {
  const { t } = useTranslation();
  const [from, setFrom] = useState(filters.period_from ?? "");
  const [to, setTo] = useState(filters.period_to ?? "");
  const p = data.filters.period;
  const dims = filters.dims ?? {};
  return (
    <div className="filters" data-testid="filters">
      {p && (
        <div className="filter">
          <span>{p.label}</span>
          <input type="date" min={p.min} max={p.max} value={from} onChange={(e) => setFrom(e.target.value)} aria-label={t("filters.from")} data-testid="period-from" />
          <input type="date" min={p.min} max={p.max} value={to} onChange={(e) => setTo(e.target.value)} aria-label={t("filters.to")} data-testid="period-to" />
          <button className="btn btn-small" onClick={() => onApply({ ...filters, period_from: from || null, period_to: to || null })} data-testid="apply-period">
            {t("filters.apply")}
          </button>
        </div>
      )}
      {data.filters.dims.map((d) => (
        <details className="filter dim" key={d.field}>
          <summary data-testid={`dim-${d.field}`}>
            {d.label}
            {dims[d.field]?.length ? ` (${dims[d.field].length})` : ""}
          </summary>
          <div className="dim-values">
            {d.values
              .filter((v) => v.value !== null)
              .map((v) => {
                const on = dims[d.field]?.includes(v.value as string) ?? false;
                return (
                  <label key={v.value as string}>
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => {
                        const cur = dims[d.field] ?? [];
                        const next = on ? cur.filter((x) => x !== v.value) : [...cur, v.value as string];
                        onApply({ ...filters, dims: { ...dims, [d.field]: next } });
                      }}
                    />
                    {v.value} <span className="muted">({v.count})</span>
                  </label>
                );
              })}
          </div>
        </details>
      ))}
      {(filters.period_from || filters.period_to || Object.values(dims).some((x) => x.length)) && (
        <button
          className="btn btn-ghost btn-small"
          onClick={() => {
            setFrom("");
            setTo("");
            onApply({});
          }}
          data-testid="reset-filters"
        >
          {t("filters.reset")}
        </button>
      )}
    </div>
  );
}

function QualityPanel({ data }: { data: Dashboard }) {
  const { t } = useTranslation();
  const q = data.quality;
  const items: string[] = [];
  for (const [reason, n] of Object.entries(q.excluded_rows)) if (reason !== "blank" && reason !== "title") items.push(t(`excluded.${reason}`, { count: n }));
  if (q.duplicates) items.push(t("quality.duplicates", { count: q.duplicates }));
  for (const c of q.columns) {
    if (c.invalid) items.push(t("quality.invalid", { name: c.name, count: c.invalid }));
    if (c.formula_missing) items.push(t("quality.formula_missing", { name: c.name, count: c.formula_missing }));
    if (c.ocr_low) items.push(t("quality.ocr_low", { name: c.name, count: c.ocr_low }));
    if (c.currencies.length > 1) items.push(t("quality.currencies", { name: c.name, list: c.currencies.join(", ") }));
  }
  for (const i of data.issues) items.push(t(`issues.${i.code}`, { ...(i.params as Record<string, unknown>), defaultValue: i.code }));
  return (
    <details className="quality" data-testid="quality">
      <summary>
        {t("quality.title")} {items.length ? `(${items.length})` : `— ${t("quality.none")}`}
      </summary>
      <ul>
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
      <p className="muted small">{t("quality.disclaimer")}</p>
    </details>
  );
}

function DetailTable({ detail, onPage }: { detail: Detail; onPage: (offset: number) => void }) {
  const { t, i18n } = useTranslation();
  const page = 50;
  return (
    <div className="detail">
      <h2>{t("dashboard.detail", { total: detail.total })}</h2>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              {detail.columns.map((c) => (
                <th key={c.id}>{c.label}</th>
              ))}
              <th>{t("dashboard.source")}</th>
            </tr>
          </thead>
          <tbody>
            {detail.rows.map((r) => (
              <tr key={r.row}>
                {r.values.map((v, i) => (
                  <td key={i} className={v === null ? "null" : ["number", "money", "percent"].includes(detail.columns[i].type) ? "num" : ""}>
                    {formatCell(v, detail.columns[i].type, i18n.language)}
                  </td>
                ))}
                <td className="muted small">{r.src}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row gap">
        <button className="btn btn-ghost" disabled={detail.offset === 0} onClick={() => onPage(Math.max(0, detail.offset - page))}>
          ←
        </button>
        <span className="muted">
          {detail.total ? detail.offset + 1 : 0}–{Math.min(detail.total, detail.offset + page)} / {detail.total}
        </span>
        <button className="btn btn-ghost" disabled={detail.offset + page >= detail.total} onClick={() => onPage(detail.offset + page)}>
          →
        </button>
      </div>
    </div>
  );
}

interface Evidence {
  metric: { label: string; unit: string; compare: string };
  definition: Parameters<typeof describeExpr>[0];
  fields: { id: string; label: string; type: string }[];
  scope_filters: { label: string; op: string; values: string[]; field: string }[];
  queries: { aggregate: unknown; where_sql: string; params: unknown[] }[];
  currency_key: string | null;
  dataset_version: number;
  exclusions: { rows_excluded_by_reason: Record<string, number>; nulls_ignored: Record<string, number>; invalid_values: Record<string, number>; formula_missing: Record<string, number> };
  rules: string[];
  contributing_rows: { total: number; offset: number; columns: string[]; rows: { row: number; src: string; values: unknown[] }[] };
}

function EvidenceModal({ base, metricId, filters, names, onClose }: { base: string; metricId: string; filters: Filters; names: Record<string, string>; onClose: () => void }) {
  const { t } = useTranslation();
  const [ev, setEv] = useState<Evidence | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    send<Evidence>("POST", `${base}/evidence/${metricId}`, { filters, offset }).then(setEv).catch(setError);
  }, [base, metricId, filters, offset]);
  const defToExpr = (d: unknown): Parameters<typeof describeExpr>[0] => {
    const x = d as Record<string, unknown>;
    if ("const" in x) return { kind: "const", value: String(x.const) };
    if ("fn" in x) return { kind: "agg", fn: String(x.fn), field: (x.field as string) ?? null, per: (x.per as string) ?? null, filters: (x.filters as never[]) ?? [] };
    return { kind: "op", op: String(x.op), left: defToExpr(x.left), right: defToExpr(x.right) };
  };
  return (
    <Modal title={t("evidence.title", { label: ev?.metric.label ?? "" })} onClose={onClose} wide>
      <ErrorBox error={error} />
      {ev && (
        <div className="evidence" data-testid="evidence">
          <h3>{t("evidence.definition")}</h3>
          <p>{describeExpr(defToExpr(ev.definition), names, t)}</p>
          <h3>{t("evidence.fields")}</h3>
          <p>{ev.fields.map((f) => `${f.label} (${t(`types.${f.type}`)})`).join(", ")}</p>
          <h3>{t("evidence.filters")}</h3>
          <p>{ev.scope_filters.length ? ev.scope_filters.map((f) => describeFilter(f, names, t)).join(" · ") : t("evidence.no_filter")}</p>
          <h3>{t("evidence.exclusions")}</h3>
          <ul>
            {Object.entries(ev.exclusions.rows_excluded_by_reason).map(([k, n]) => (
              <li key={k}>{t(`excluded.${k}`, { count: n })}</li>
            ))}
            {Object.entries(ev.exclusions.nulls_ignored).map(([k, n]) => (
              <li key={k}>{t("evidence.nulls_ignored", { name: names[k] ?? k, count: n })}</li>
            ))}
            {Object.entries(ev.exclusions.invalid_values).map(([k, n]) => (
              <li key={k}>{t("evidence.invalid", { name: names[k] ?? k, count: n })}</li>
            ))}
            {Object.entries(ev.exclusions.formula_missing).map(([k, n]) => (
              <li key={k}>{t("quality.formula_missing", { name: names[k] ?? k, count: n })}</li>
            ))}
          </ul>
          <h3>{t("evidence.rules")}</h3>
          <ul>
            {ev.rules.map((r) => (
              <li key={r}>{t(`rules.${r}`)}</li>
            ))}
          </ul>
          <details>
            <summary>{t("evidence.query")}</summary>
            {ev.queries.map((q, i) => (
              <pre key={i} className="code">
                WHERE {q.where_sql}
                {"\n"}params: {JSON.stringify(q.params)}
              </pre>
            ))}
            <p className="muted small">{t("evidence.version", { version: ev.dataset_version })}</p>
          </details>
          <h3>{t("evidence.sources", { total: ev.contributing_rows.total })}</h3>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("dashboard.source")}</th>
                  {ev.contributing_rows.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ev.contributing_rows.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="small">{r.src}</td>
                    {r.values.map((v, i) => (
                      <td key={i}>{formatCell(v, ev.fields[i]?.type, "fr") === "—" ? "—" : String(v ?? "—")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row gap">
            <button className="btn btn-ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
              ←
            </button>
            <button className="btn btn-ghost" disabled={offset + 50 >= ev.contributing_rows.total} onClick={() => setOffset(offset + 50)}>
              →
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
