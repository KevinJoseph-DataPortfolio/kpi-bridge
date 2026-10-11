import { type ReactNode, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api";
import { LANGUAGES, setLanguage } from "../i18n";
import type { Job, Note } from "../types";

export function errorText(t: (k: string, o?: Record<string, unknown>) => string, err: unknown): string {
  const e = err as { code?: string; params?: Record<string, unknown> } | null;
  if (err instanceof ApiError || (e && typeof e.code === "string")) {
    const code = (e as { code: string }).code;
    const key = `errors.${code}`;
    const txt = t(key, (e?.params || {}) as Record<string, unknown>);
    return txt === key ? t("errors.generic", { code }) : txt;
  }
  return t("errors.generic", { code: "client" });
}

export function ErrorBox({ error, onClose }: { error: unknown; onClose?: () => void }) {
  const { t } = useTranslation();
  if (!error) return null;
  return (
    <div className="alert alert-error" role="alert">
      <span>{errorText(t, error)}</span>
      {onClose && (
        <button className="link" onClick={onClose} aria-label={t("common.close")}>
          ×
        </button>
      )}
    </div>
  );
}

export function LangSelect() {
  const { t, i18n } = useTranslation();
  return (
    <label className="lang-select">
      <span className="sr-only">{t("common.language")}</span>
      <select value={i18n.language} onChange={(e) => setLanguage(e.target.value)} aria-label={t("common.language")} data-testid="lang-select">
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function JobProgress({ job, onCancel }: { job: Job; onCancel?: () => void }) {
  const { t } = useTranslation();
  const pct = Math.round(job.progress * 100);
  const stages = job.kind === "interpret" ? ["queued", "ai", "validate"] : ["queued", "import", "extract", "ocr", "verify"];
  return (
    <div className="job" data-testid="job-progress">
      <div className="job-head">
        <strong>{t(`jobs.kind.${job.kind}`)}</strong>
        <span className="muted">
          {t(`jobs.stage.${job.stage}`, { defaultValue: job.stage })} · {pct} %
        </span>
      </div>
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="progress-bar" style={{ width: `${pct}%` }} />
      </div>
      <ol className="stages">
        {stages.map((s) => (
          <li key={s} className={s === job.stage ? "current" : ""}>
            {t(`jobs.stage.${s}`)}
          </li>
        ))}
      </ol>
      {onCancel && (job.status === "queued" || job.status === "running") && (
        <button className="btn btn-ghost" onClick={onCancel} data-testid="cancel-job">
          {t("common.cancel")}
        </button>
      )}
    </div>
  );
}

export function NoteList({ notes }: { notes: Note[] }) {
  const { t } = useTranslation();
  const shown = notes.filter((n) => !["format", "sheet"].includes(n.code));
  if (!shown.length) return null;
  return (
    <ul className="notes">
      {shown.map((n, i) => (
        <li key={i}>{t(`notes.${n.code}`, { ...flatParams(n.params), defaultValue: n.code })}</li>
      ))}
    </ul>
  );
}

function flatParams(p: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(p)) out[k] = Array.isArray(v) ? v.join(", ") : typeof v === "object" && v !== null ? "" : v;
  return out;
}

export function Stepper({ step }: { step: number }) {
  const { t } = useTranslation();
  const steps = ["steps.upload", "steps.ask", "steps.verify", "steps.dashboard"];
  return (
    <ol className="stepper" aria-label={t("steps.label")}>
      {steps.map((s, i) => (
        <li key={s} className={i === step ? "active" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
          <span className="num">{i + 1}</span> {t(s)}
        </li>
      ))}
    </ol>
  );
}
