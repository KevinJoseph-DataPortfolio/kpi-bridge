import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, send } from "../api";
import { ErrorBox, JobProgress } from "../components/common";
import { describeExpr, describeFilter } from "../describe";
import type { Job, Plan, Question, TableInfo } from "../types";
import Builder from "./Builder";

interface Props {
  projectId: string;
  planId: string;
  onValidated: () => void;
  onModify: () => void;
  onBuilder: () => void;
  onFix: (tableId: string) => void;
}

export default function PlanReview({ projectId, planId, onValidated, onModify, onBuilder, onFix }: Props) {
  const { t, i18n } = useTranslation();
  const [plan, setPlan] = useState<(Plan & { job: Job | null }) | null>(null);
  const [table, setTable] = useState<TableInfo | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const p = await get<Plan & { job: Job | null }>(`/projects/${projectId}/plans/${planId}`);
    setPlan(p);
    return p;
  }, [projectId, planId]);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const p = await load();
        if (!stop && p.status === "interpreting") timer = setTimeout(tick, 800);
      } catch (e) {
        setError(e);
      }
    };
    void tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [load]);

  useEffect(() => {
    if (!plan) return;
    get<{ tables: TableInfo[] }>(`/projects/${projectId}`)
      .then((p) => setTable(p.tables.find((x) => x.id === plan.table_id) ?? null))
      .catch(() => undefined);
  }, [projectId, plan?.table_id, plan?.dataset_version]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!plan) return <div className="muted">{t("common.loading")}</div>;
  const names: Record<string, string> = Object.fromEntries((table?.columns || []).map((c) => [c.id, c.name]));

  async function submitAnswers() {
    setBusy(true);
    setError(null);
    try {
      const p = await send<Plan & { job: Job | null }>("POST", `/projects/${projectId}/plans/${planId}/answers`, { answers });
      setPlan(p);
      setAnswers({});
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    setBusy(true);
    try {
      await send("POST", `/projects/${projectId}/plans/${planId}/validate`);
      onValidated();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const unavailable = plan.issues.filter((i) => i.code === "kpi_unavailable");
  const blocking = plan.issues.filter((i) => i.severity === "error");
  const warnings = plan.issues.filter((i) => i.severity === "warning" && i.code !== "kpi_unavailable");

  return (
    <section className="screen plan-review" data-testid="plan-review">
      <h1>{t("plan.title")}</h1>
      {plan.request?.text && (
        <blockquote className="request-quote">
          {plan.request.text}
        </blockquote>
      )}
      <p className="muted">{t(`plan.origin.${plan.origin.split(":")[0]}`, { model: plan.model ?? "" })}</p>
      {plan.status === "interpreting" && plan.job && (
        <JobProgress job={plan.job} onCancel={() => void send("POST", `/jobs/${plan.job!.id}/cancel`).then(load)} />
      )}
      {(plan.status === "ai_failed" || plan.status === "not_understood" || plan.status === "invalid") && (
        <>
          <div className="alert alert-error" data-testid="ai-failed">
            <p>{t(`plan.status.${plan.status}`)}</p>
            <ul>
              {blocking.map((i, k) => (
                <li key={k}>{t(`issues.${i.code}`, { ...flat(i.params), defaultValue: t("errors.generic", { code: i.code }) })}</li>
              ))}
            </ul>
            <p>{t("plan.fallback_explained")}</p>
            <button className="btn" onClick={onModify}>
              {t("plan.modify")}
            </button>
          </div>
          <Builder projectId={projectId} tableId={plan.table_id} replacePlan={planId} onDone={() => void load()} onBack={onModify} embedded />
        </>
      )}
      {plan.status === "needs_clarification" && (
        <div className="questions" data-testid="questions">
          <h2>{t("plan.questions_title", { count: plan.questions.length })}</h2>
          {plan.questions.map((q) => (
            <QuestionBox key={q.key} q={q} value={answers[q.key]} onChange={(v) => setAnswers({ ...answers, [q.key]: v })} lang={i18n.language} />
          ))}
          <button className="btn btn-primary" disabled={busy || plan.questions.some((q) => !answers[q.key])} onClick={() => void submitAnswers()} data-testid="submit-answers">
            {t("plan.answer")}
          </button>
        </div>
      )}
      {plan.plan && plan.status !== "interpreting" && (
        <div className="plan-summary">
          <h2>{t("plan.kpis")}</h2>
          <ul className="kpi-defs">
            {plan.plan.metrics.map((m) => (
              <li key={m.id}>
                <strong>{m.label}</strong> <span className="type-tag">{t(`units.${m.unit}`)}</span>
                <div className="muted">{describeExpr(m.expression, names, t)}</div>
                {m.filters.length > 0 && <div className="muted">{m.filters.map((f) => describeFilter(f, names, t)).join(" · ")}</div>}
                {m.compare === "previous_period" && <div className="muted">{t("plan.compare")}</div>}
              </li>
            ))}
          </ul>
          {plan.plan.charts.length > 0 && (
            <>
              <h2>{t("plan.charts")}</h2>
              <ul>
                {plan.plan.charts.map((c) => (
                  <li key={c.id}>
                    {t(`chart_types.${c.type}`)} — {plan.plan!.metrics.find((m) => m.id === c.metric)?.label} {t("plan.by")}{" "}
                    {c.dimension === "__period__" ? t(`grains.${plan.plan!.time_grain}`) : names[c.dimension] ?? c.dimension}
                    {c.top_n ? ` (top ${c.top_n})` : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          {plan.plan.global_filters.length > 0 && (
            <p className="muted">
              {t("plan.global_filters")} {plan.plan.global_filters.map((f) => describeFilter(f, names, t)).join(" · ")}
            </p>
          )}
          {plan.plan.period_field && <p className="muted">{t("plan.period_field", { field: names[plan.plan.period_field] ?? plan.plan.period_field })}</p>}
        </div>
      )}
      {unavailable.length > 0 && (
        <div className="alert alert-warn" data-testid="kpi-unavailable">
          {unavailable.map((u, k) => (
            <p key={k}>
              {t("plan.kpi_unavailable", { what: String(u.params.what ?? ""), missing: ((u.params.missing as string[]) || []).join(", ") })}
            </p>
          ))}
          <p>{t("plan.alternatives_hint")}</p>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="warnings">
          {warnings.map((w, k) => (
            <li key={k}>{t(`issues.${w.code}`, { ...flat(w.params), defaultValue: w.code })}</li>
          ))}
        </ul>
      )}
      <ErrorBox error={error} onClose={() => setError(null)} />
      <div className="row gap">
        {plan.status === "ready" && (
          <button className="btn btn-primary" disabled={busy} onClick={() => void validate()} data-testid="validate-plan">
            {t("plan.validate")}
          </button>
        )}
        {plan.status === "validated" && (
          <button className="btn btn-primary" onClick={onValidated}>
            {t("plan.open_dashboard")}
          </button>
        )}
        <button className="btn btn-ghost" onClick={onModify} data-testid="modify-request">
          {t("plan.modify")}
        </button>
        <button className="btn btn-ghost" onClick={onBuilder}>
          {t("understand.builder")}
        </button>
        <button className="btn btn-ghost" onClick={() => onFix(plan.table_id)}>
          {t("understand.check_data")}
        </button>
      </div>
    </section>
  );
  function flat(p: Record<string, unknown>) {
    const o: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(p || {})) o[k] = Array.isArray(v) ? v.join(", ") : typeof v === "object" ? "" : v;
    return o;
  }
}

function QuestionBox({ q, value, onChange, lang }: { q: Question; value?: string; onChange: (v: string) => void; lang: string }) {
  const { t } = useTranslation();
  const p = q.params as Record<string, unknown>;
  let explanation = t(`questions.${q.kind}`, { ...p, values: Array.isArray(p.values) ? (p.values as string[]).join(", ") : "" });
  if (q.kind === "date_order") {
    const ex = ((p.examples as string[]) || [])[0] || "";
    const m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})/.exec(ex);
    if (m) {
      const y = m[3].length === 2 ? 2000 + Number(m[3]) : Number(m[3]);
      const fmt = new Intl.DateTimeFormat(lang === "zh-Hans" ? "zh-CN" : lang, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
      const dmy = fmt.format(new Date(Date.UTC(y, Number(m[2]) - 1, Number(m[1]))));
      const mdy = fmt.format(new Date(Date.UTC(y, Number(m[1]) - 1, Number(m[2]))));
      explanation = t("questions.date_order_example", { example: ex, dmy, mdy, name: p.name });
    }
  }
  return (
    <fieldset className="question" data-testid={`question-${q.kind}`}>
      <legend>{explanation}</legend>
      <div className="options">
        {q.options.map((o) => (
          <label key={o} className={`option ${value === o ? "on" : ""}`}>
            <input type="radio" name={q.key} value={o} checked={value === o} onChange={() => onChange(o)} />
            {(p.option_labels as Record<string, string> | undefined)?.[o] ?? t(`questions.options.${q.kind}.${o}`, { defaultValue: o })}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
