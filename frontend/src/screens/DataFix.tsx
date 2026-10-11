import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, send } from "../api";
import { ErrorBox, Modal } from "../components/common";
import type { Project } from "../types";

interface ColumnSpec {
  id: string;
  raw_index: number;
  name: string;
  type: string;
  convention: string | null;
  date_order: string | null;
  currency: string | null;
}
interface TableConfig {
  header_row: number;
  header_depth: number;
  columns: ColumnSpec[];
  excluded: Record<string, string>;
  corrections: Record<string, { value: string; original: string | null }>;
  confirmed: Record<string, unknown>;
}
interface RawView {
  total: number;
  offset: number;
  header_row: number;
  rows: { index: number; src: string; cells: string[]; excluded: string | null; flags: Record<string, string>; corrections: Record<string, string> }[];
  col_refs: string[];
}
interface ConfigResp {
  version: number;
  config: TableConfig;
  profile: { columns: { id: string; invalid: number; ocr_low: number; formula_missing: number; invalid_samples?: { row: number; raw?: string }[] }[] };
  versions: { version: number; op?: string; created_at: number }[];
}

const TYPES = ["text", "id", "number", "money", "percent", "date", "bool", "ignore"];
const PAGE = 60;

export default function DataFix({ project, tableId, onBack, onReload }: { project: Project; tableId: string; onBack: () => void; onReload: () => Promise<Project | null> }) {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<ConfigResp | null>(null);
  const [draft, setDraft] = useState<TableConfig | null>(null);
  const [raw, setRaw] = useState<RawView | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [cell, setCell] = useState<{ r: number; c: number; value: string; original: string } | null>(null);
  const table = project.tables.find((x) => x.id === tableId);
  const base = `/projects/${project.id}/tables/${tableId}`;

  const load = useCallback(async () => {
    const c = await get<ConfigResp>(`${base}/config`);
    setCfg(c);
    setDraft(structuredClone(c.config));
    setRaw(await get<RawView>(`${base}/raw?offset=${offset}&limit=${PAGE}`));
  }, [base, offset]);

  useEffect(() => {
    load().catch(setError);
  }, [load]);

  async function save(next: TableConfig, op: string) {
    setError(null);
    try {
      await send("PUT", `${base}/config`, { config: next, base_version: cfg?.version, op });
      await load();
      await onReload();
    } catch (e) {
      setError(e);
    }
  }

  if (!cfg || !draft || !raw) return <div className="muted">{t("common.loading")}</div>;
  const quality = Object.fromEntries(cfg.profile.columns.map((c) => [c.id, c]));

  return (
    <section className="screen datafix" data-testid="datafix">
      <h1>{t("fix.title", { name: table?.name ?? "" })}</h1>
      <p className="hint">{t("fix.hint")}</p>
      {table && table.questions.length > 0 && (
        <div className="alert alert-warn">
          {table.questions.map((q) => (
            <p key={q.key}>{t(`fix.question.${q.kind}`, { name: String(q.params.name ?? ""), examples: ((q.params.examples as string[]) || []).join(", ") })}</p>
          ))}
        </div>
      )}
      <h2>{t("fix.columns")}</h2>
      <div className="table-wrap">
        <table className="data colspec">
          <thead>
            <tr>
              <th>{t("fix.column")}</th>
              <th>{t("fix.type")}</th>
              <th>{t("fix.convention")}</th>
              <th>{t("fix.currency")}</th>
              <th>{t("fix.quality")}</th>
            </tr>
          </thead>
          <tbody>
            {draft.columns.map((c, i) => {
              const q = quality[c.id];
              const set = (patch: Partial<ColumnSpec>) => setDraft({ ...draft, columns: draft.columns.map((x, k) => (k === i ? { ...x, ...patch } : x)) });
              return (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>
                    <select value={c.type} onChange={(e) => set({ type: e.target.value })} aria-label={t("fix.type")} data-testid={`type-${c.id}`}>
                      {TYPES.map((ty) => (
                        <option key={ty} value={ty}>
                          {t(`types.${ty}`)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {["number", "money", "percent"].includes(c.type) && (
                      <select value={c.convention ?? "dot"} onChange={(e) => set({ convention: e.target.value })} aria-label={t("fix.convention")} data-testid={`conv-${c.id}`}>
                        <option value="dot">1,234.56</option>
                        <option value="comma">1 234,56</option>
                      </select>
                    )}
                    {c.type === "date" && (
                      <select value={c.date_order ?? "DMY"} onChange={(e) => set({ date_order: e.target.value })} aria-label={t("fix.date_order")} data-testid={`order-${c.id}`}>
                        <option value="DMY">{t("questions.options.date_order.DMY")}</option>
                        <option value="MDY">{t("questions.options.date_order.MDY")}</option>
                      </select>
                    )}
                  </td>
                  <td>
                    {["money", "number"].includes(c.type) && (
                      <input className="small" value={c.currency ?? ""} maxLength={3} placeholder="EUR" onChange={(e) => set({ currency: e.target.value.toUpperCase() || null, type: e.target.value ? "money" : c.type })} />
                    )}
                  </td>
                  <td className="muted">
                    {q && q.invalid > 0 && <span className="warn">{t("fix.invalid", { count: q.invalid })} </span>}
                    {q && q.ocr_low > 0 && <span className="warn">{t("fix.ocr_low", { count: q.ocr_low })} </span>}
                    {q && q.formula_missing > 0 && <span className="warn">{t("fix.formula_missing", { count: q.formula_missing })}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button
        className="btn btn-primary"
        data-testid="save-columns"
        onClick={() => {
          const confirmed = { ...draft.confirmed };
          for (const c of draft.columns) {
            if (c.type === "date") confirmed[`date_order:${c.id}`] = c.date_order ?? "DMY";
            if (["number", "money", "percent"].includes(c.type)) confirmed[`number_convention:${c.id}`] = c.convention ?? "dot";
          }
          confirmed["header_row"] = true;
          void save({ ...draft, confirmed }, "columns");
        }}
      >
        {t("fix.save")}
      </button>
      <h2>{t("fix.raw_title")}</h2>
      <p className="hint">{t("fix.raw_hint")}</p>
      <div className="table-wrap">
        <table className="data raw">
          <thead>
            <tr>
              <th>#</th>
              <th>{t("fix.status")}</th>
              {raw.col_refs.map((r, j) => (
                <th key={j}>{r}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {raw.rows.map((r) => {
              const isHeader = r.index === raw.header_row;
              const excluded = draft.excluded[String(r.index)];
              return (
                <tr key={r.index} className={isHeader ? "header-row" : excluded ? "excluded" : ""} data-testid={`raw-row-${r.index}`}>
                  <td title={r.src}>{r.index + 1}</td>
                  <td>
                    {isHeader ? (
                      <span className="badge">{t("fix.header")}</span>
                    ) : (
                      <>
                        {excluded && <span className="badge badge-warn">{t(`excluded_reason.${excluded}`)}</span>}
                        <button
                          className="link small"
                          onClick={() => {
                            const ex = { ...draft.excluded };
                            if (excluded) delete ex[String(r.index)];
                            else ex[String(r.index)] = "manual";
                            void save({ ...draft, excluded: ex }, excluded ? "include_row" : "exclude_row");
                          }}
                        >
                          {excluded ? t("fix.include") : t("fix.exclude")}
                        </button>
                        <button className="link small" onClick={() => send("POST", `${base}/header`, { header_row: r.index, base_version: cfg.version }).then(load).then(onReload).catch(setError)}>
                          {t("fix.use_as_header")}
                        </button>
                      </>
                    )}
                  </td>
                  {r.cells.map((v, j) => {
                    const corrected = r.corrections[String(j)];
                    const flag = r.flags[String(j)];
                    return (
                      <td key={j} className={`${flag ? `flag-${flag}` : ""} ${corrected !== undefined ? "corrected" : ""}`}>
                        <button className="cell-btn" onClick={() => setCell({ r: r.index, c: j, value: corrected ?? v, original: v })} title={flag ? t(`fix.flag.${flag}`) : t("fix.edit_cell")}>
                          {corrected !== undefined ? corrected : v}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row gap">
        <button className="btn btn-ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          ←
        </button>
        <span className="muted">
          {offset + 1}–{Math.min(raw.total, offset + PAGE)} / {raw.total}
        </span>
        <button className="btn btn-ghost" disabled={offset + PAGE >= raw.total} onClick={() => setOffset(offset + PAGE)}>
          →
        </button>
      </div>
      <h2>{t("fix.versions")}</h2>
      <ul className="versions">
        {cfg.versions.map((v) => (
          <li key={v.version}>
            v{v.version} · {t(`fix.ops.${(v.op || "edit").split(":")[0]}`, { defaultValue: v.op })} · {new Date(v.created_at * 1000).toLocaleTimeString()}
            {v.version !== cfg.version && (
              <button className="link small" onClick={() => send("POST", `${base}/revert/${v.version}`).then(load).then(onReload).catch(setError)}>
                {t("fix.revert")}
              </button>
            )}
            {v.version === cfg.version && <span className="badge">{t("fix.current")}</span>}
          </li>
        ))}
      </ul>
      <ErrorBox error={error} onClose={() => setError(null)} />
      <button className="btn" onClick={onBack} data-testid="fix-back">
        {t("common.back")}
      </button>
      {cell && <CellEditor base={base} cell={cell} onClose={() => setCell(null)} onSave={(value, original) => {
        const key = `${cell.r},${cell.c}`;
        const corrections = { ...draft.corrections };
        if (value === original) delete corrections[key];
        else corrections[key] = { value, original };
        setCell(null);
        void save({ ...draft, corrections }, "correct_cell");
      }} />}
    </section>
  );
}

function CellEditor({ base, cell, onClose, onSave }: { base: string; cell: { r: number; c: number; value: string; original: string }; onClose: () => void; onSave: (v: string, original: string) => void }) {
  const { t } = useTranslation();
  const [info, setInfo] = useState<{ src: string | null; meta: { conf?: number; page?: number } | null; has_image: boolean } | null>(null);
  const [value, setValue] = useState(cell.value);
  useEffect(() => {
    get<typeof info>(`${base}/cells/${cell.r}/${cell.c}`).then(setInfo).catch(() => undefined);
  }, [base, cell.r, cell.c]);
  return (
    <Modal title={t("fix.edit_cell")} onClose={onClose}>
      {info?.src && <p className="muted">{t("fix.source", { src: info.src })}</p>}
      {info?.meta?.conf !== undefined && <p className="muted">{t("fix.ocr_conf", { conf: info.meta.conf })}</p>}
      {info?.has_image && <img className="cell-image" alt={t("fix.source_image")} src={`/api/v1${base}/cells/${cell.r}/${cell.c}/image`} />}
      <label>
        {t("fix.new_value")}
        <input value={value} onChange={(e) => setValue(e.target.value)} data-testid="cell-value" />
      </label>
      <div className="row gap">
        <button className="btn btn-primary" onClick={() => onSave(value, cell.original)} data-testid="cell-save">
          {t("common.save")}
        </button>
        <button className="btn btn-ghost" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </Modal>
  );
}
