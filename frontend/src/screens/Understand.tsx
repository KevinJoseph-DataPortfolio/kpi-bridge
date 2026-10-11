import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, send } from "../api";
import { ErrorBox, NoteList } from "../components/common";
import { formatCell, formatDate } from "../format";
import type { Meta, Project, TableInfo } from "../types";

interface Props {
  project: Project;
  meta: Meta | null;
  tableId: string | null;
  onTable: (id: string) => void;
  onReload: () => Promise<Project | null>;
  onPlan: (id: string) => void;
  onBuilder: () => void;
  onFix: () => void;
  onAddFile: () => void;
}

interface Suggestion {
  id: string;
  label: string;
}

interface Preview {
  total: number;
  columns: { id: string; label: string }[];
  rows: { row: number; src: string; values: unknown[] }[];
}

export default function Understand({ project, meta, tableId, onTable, onReload, onPlan, onBuilder, onFix, onAddFile }: Props) {
  const { t, i18n } = useTranslation();
  const table = project.tables.find((tb) => tb.id === tableId) ?? project.tables[0];
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!table) return;
    get<Suggestion[]>(`/projects/${project.id}/tables/${table.id}/suggestions?lang=${i18n.language}`).then(setSuggestions).catch(setError);
    get<Preview>(`/projects/${project.id}/tables/${table.id}/preview?limit=15`).then(setPreview).catch(setError);
    setChosen([]);
  }, [project.id, table?.id, table?.version, i18n.language]); // eslint-disable-line react-hooks/exhaustive-deps

  const types = useMemo(() => Object.fromEntries((table?.columns || []).map((c) => [c.id, c.type])), [table]);

  if (!table) {
    return (
      <section className="screen">
        <p>{t("understand.no_table")}</p>
        <button className="btn" onClick={onAddFile}>
          {t("understand.add_file")}
        </button>
      </section>
    );
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await send<{ plan_id: string }>("POST", `/projects/${project.id}/requests`, {
        table_id: table.id,
        text,
        suggestion_ids: chosen,
        ui_lang: i18n.language,
      });
      onPlan(res.plan_id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const aiReady = meta?.ai.reachable && meta.ai.model_present;

  return (
    <section className="screen understand">
      <h1>{t("understand.title")}</h1>
      <div className="tables-list" role="list">
        {project.tables.map((tb) => (
          <TableCard key={tb.id} table={tb} active={tb.id === table.id} onClick={() => onTable(tb.id)} />
        ))}
      </div>
      <div className="row gap">
        <button className="btn btn-ghost" onClick={onAddFile}>
          {t("understand.add_file")}
        </button>
        <button className="btn btn-ghost" onClick={onFix} data-testid="open-fix">
          {t("understand.check_data")}
        </button>
      </div>
      {table.questions.length > 0 && (
        <div className="alert alert-warn">
          {t("understand.structure_questions", { count: table.questions.length })}{" "}
          <button className="link" onClick={onFix}>
            {t("understand.check_data")}
          </button>
        </div>
      )}
      {preview && (
        <div className="preview">
          <h2>{t("understand.preview", { shown: preview.rows.length, total: preview.total })}</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c.id} title={t(`types.${types[c.id] ?? "text"}`)}>
                      {c.label}
                      <span className="type-tag">{t(`types.${types[c.id] ?? "text"}`)}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={r.row}>
                    {r.values.map((v, i) => (
                      <td key={i} className={v === null ? "null" : ""}>
                        {formatCell(v, types[preview.columns[i].id], i18n.language)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="ask">
        <h2>{t("understand.question")}</h2>
        <textarea
          data-testid="request-input"
          value={text}
          maxLength={meta?.limits.max_request_chars ?? 2000}
          placeholder={t("understand.placeholder")}
          onChange={(e) => setText(e.target.value)}
          rows={3}
        />
        <div className="chips" role="group" aria-label={t("understand.suggestions")}>
          {suggestions.map((s) => (
            <button
              key={s.id}
              className={`chip ${chosen.includes(s.id) ? "on" : ""}`}
              aria-pressed={chosen.includes(s.id)}
              data-testid={`suggestion-${s.id}`}
              onClick={() => setChosen(chosen.includes(s.id) ? chosen.filter((x) => x !== s.id) : [...chosen, s.id])}
            >
              {s.label}
            </button>
          ))}
        </div>
        <p className="hint">{text.trim() ? (aiReady ? t("understand.ai_mode") : t("understand.ai_unavailable")) : t("understand.suggestion_mode")}</p>
        <div className="row gap">
          <button className="btn btn-primary" disabled={busy || (!text.trim() && !chosen.length)} onClick={() => void submit()} data-testid="submit-request">
            {t("understand.submit")}
          </button>
          <button className="btn btn-ghost" onClick={onBuilder} data-testid="open-builder">
            {t("understand.builder")}
          </button>
        </div>
        <ErrorBox error={error} onClose={() => setError(null)} />
      </div>
      {project.tables.length > 1 && <JoinPanel project={project} onDone={() => void onReload()} />}
      <RecipePanel project={project} tableId={table.id} onPlan={onPlan} />
    </section>
  );
}

function TableCard({ table, active, onClick }: { table: TableInfo; active: boolean; onClick: () => void }) {
  const { t, i18n } = useTranslation();
  const cov = Object.values(table.coverage)[0];
  const excluded = Object.entries(table.excluded).filter(([k]) => k !== "title" && k !== "blank");
  return (
    <div className={`table-card ${active ? "active" : ""}`} role="listitem" data-testid="table-card">
      <button className="link table-title" onClick={onClick} aria-pressed={active}>
        <strong>{table.name}</strong>
      </button>
      <span>{t("understand.size", { rows: table.n_rows, cols: table.n_cols })}</span>
      {cov && <span>{t("understand.period", { from: formatDate(cov.min, i18n.language), to: formatDate(cov.max, i18n.language) })}</span>}
      {cov && cov.missing_months.length > 0 && <span className="warn">{t("understand.missing_months", { months: cov.missing_months.join(", ") })}</span>}
      {excluded.map(([reason, n]) => (
        <span key={reason} className="muted">
          {t(`excluded.${reason}`, { count: n })}
        </span>
      ))}
      {table.duplicates.groups > 0 && <span className="warn">{t("understand.duplicates", { count: table.duplicates.rows })}</span>}
      <NoteList notes={table.notes} />
    </div>
  );
}

function JoinPanel({ project, onDone }: { project: Project; onDone: () => void }) {
  const { t } = useTranslation();
  const [left, setLeft] = useState(project.tables[0].id);
  const [right, setRight] = useState(project.tables[1].id);
  const [lk, setLk] = useState("");
  const [rk, setRk] = useState("");
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const lt = project.tables.find((x) => x.id === left);
  const rt = project.tables.find((x) => x.id === right);
  const body = () => ({ left_table: left, left_key: lk, right_table: right, right_key: rk, right_columns: (rt?.columns || []).map((c) => c.id).filter((c) => c !== rk) });
  return (
    <details className="panel">
      <summary>{t("join.title")}</summary>
      <p className="hint">{t("join.hint")}</p>
      <div className="grid2">
        <label>
          {t("join.left")}
          <select value={left} onChange={(e) => setLeft(e.target.value)}>
            {project.tables.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
          <select value={lk} onChange={(e) => setLk(e.target.value)} aria-label={t("join.left_key")}>
            <option value="">{t("join.key")}</option>
            {lt?.columns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("join.right")}
          <select value={right} onChange={(e) => setRight(e.target.value)}>
            {project.tables.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
          <select value={rk} onChange={(e) => setRk(e.target.value)} aria-label={t("join.right_key")}>
            <option value="">{t("join.key")}</option>
            {rt?.columns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="row gap">
        <button className="btn" disabled={!lk || !rk || left === right} onClick={() => send<Record<string, unknown>>("POST", `/projects/${project.id}/joins/check`, body()).then(setReport).catch(setError)}>
          {t("join.check")}
        </button>
        {report?.allowed === true && (
          <button className="btn btn-primary" onClick={() => send("POST", `/projects/${project.id}/joins`, body()).then(onDone).catch(setError)}>
            {t("join.create")}
          </button>
        )}
      </div>
      {report && (
        <div className={`alert ${report.allowed ? "alert-ok" : "alert-error"}`} data-testid="join-report">
          {report.allowed ? t("join.ok", report) : t(`join.blocked.${String(report.reason)}`, report)}
          <div className="muted">{t("join.report", report)}</div>
        </div>
      )}
      <ErrorBox error={error} onClose={() => setError(null)} />
    </details>
  );
}

function RecipePanel({ project, tableId, onPlan }: { project: Project; tableId: string; onPlan: (id: string) => void }) {
  const { t } = useTranslation();
  const [recipes, setRecipes] = useState<{ id: string; name: string }[]>([]);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<{ problems: { field: string; name: string; problem: string; candidates: { id: string; name: string }[] }[]; needs_remap: boolean } | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    get<{ id: string; name: string }[]>("/recipes").then(setRecipes).catch(() => undefined);
  }, []);
  async function importFile(f: File) {
    try {
      const data = JSON.parse(await f.text());
      const r = await send<{ id: string; name: string }>("POST", "/recipes/import", data);
      setRecipes([r, ...recipes]);
      setSelected(r.id);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <details className="panel">
      <summary>{t("recipe.apply_title")}</summary>
      <p className="hint">{t("recipe.apply_hint")}</p>
      <div className="row gap">
        <select value={selected} onChange={(e) => setSelected(e.target.value)} data-testid="recipe-select">
          <option value="">{t("recipe.choose")}</option>
          {recipes.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
        <label className="btn btn-ghost">
          {t("recipe.import")}
          <input type="file" accept=".json" hidden onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
        </label>
        <button
          className="btn"
          disabled={!selected}
          data-testid="recipe-apply"
          onClick={async () => {
            setError(null);
            try {
              const res = await send<{ status: string; plan_id?: string; problems?: never[]; needs_remap?: boolean }>("POST", `/projects/${project.id}/recipes/${selected}/apply`, {
                table_id: tableId,
                mapping,
              });
              if (res.status === "needs_remap") setReport(res as never);
              else if (res.plan_id) onPlan(res.plan_id);
            } catch (e) {
              setError(e);
            }
          }}
        >
          {t("recipe.apply")}
        </button>
      </div>
      {report?.needs_remap && (
        <div className="alert alert-warn" data-testid="remap">
          <p>{t("recipe.remap_needed")}</p>
          {report.problems.map((p) => (
            <label key={p.field} className="remap-row">
              <span>
                {p.name} — {t(`recipe.problem.${p.problem}`)}
              </span>
              <select value={mapping[p.field] || ""} onChange={(e) => setMapping({ ...mapping, [p.field]: e.target.value })}>
                <option value="">{t("recipe.pick_column")}</option>
                {p.candidates.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
      <ErrorBox error={error} onClose={() => setError(null)} />
    </details>
  );
}
