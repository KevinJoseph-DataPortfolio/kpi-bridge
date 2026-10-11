import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, send, uploadFile } from "../api";
import { ErrorBox, JobProgress, errorText } from "../components/common";
import { useJob } from "../hooks";
import type { Job, Meta, Project, SourceFile } from "../types";

interface Props {
  meta: Meta | null;
  project: Project | null;
  onProject: (id: string) => void;
  onReady: () => void;
  onReload: () => Promise<Project | null>;
}

interface ProjectSummary {
  id: string;
  name: string;
  updated_at: number;
  n_files: number;
}

export default function Upload({ meta, project, onProject, onReady, onReload }: Props) {
  const { t, i18n } = useTranslation();
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [queue, setQueue] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    get<ProjectSummary[]>("/projects").then(setProjects).catch(() => undefined);
  }, [project?.id]);

  // reprise d'une tâche d'import active après rechargement
  useEffect(() => {
    const active = project?.active_jobs.find((j) => j.kind === "ingest");
    if (active && !jobId) setJobId(active.id);
  }, [project, jobId]);

  const job = useJob(jobId, async (j: Job) => {
    const p = await onReload();
    if (j.status === "failed") setError({ code: j.error?.code, params: j.error?.params });
    const next = queue[0];
    if (next) {
      setQueue(queue.slice(1));
      setJobId(next);
      return;
    }
    setJobId(null);
    if (j.status === "done" && p?.tables.length) onReady();
  });

  async function ensureProject(name: string): Promise<string> {
    if (project) return project.id;
    const p = await send<{ id: string }>("POST", "/projects", { name, ui_lang: i18n.language });
    onProject(p.id);
    return p.id;
  }

  async function handleFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setError(null);
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const pid = await ensureProject(list[0].name.replace(/\.[^.]+$/, ""));
      const jobs: string[] = [];
      for (const f of list) {
        if (meta && f.size > meta.limits.max_file_mib * 1024 * 1024) {
          setError({ code: "file_too_large", params: { limit_mib: meta.limits.max_file_mib } });
          continue;
        }
        setUploadPct(0);
        const res = await uploadFile(pid, f, i18n.language, setUploadPct, ctrl.signal);
        jobs.push(res.job_id);
      }
      setUploadPct(null);
      if (jobs.length) {
        setJobId(jobs[0]);
        setQueue(jobs.slice(1));
      }
    } catch (e) {
      setError(e);
      setUploadPct(null);
    } finally {
      setBusy(false);
    }
  }

  async function tryExample(name: string) {
    setError(null);
    setBusy(true);
    try {
      const pid = await ensureProject(t("upload.example_project", { name }));
      const res = await send<{ job_id: string }>("POST", `/projects/${pid}/examples/${encodeURIComponent(name)}?ui_lang=${i18n.language}`);
      setJobId(res.job_id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    abortRef.current?.abort();
    if (jobId) await send("POST", `/jobs/${jobId}/cancel`).catch(() => undefined);
  }

  const failed = (project?.files || []).filter((f) => f.status === "failed");

  return (
    <section className="screen upload">
      <h1>{project ? t("upload.add_title") : t("upload.title")}</h1>
      <p className="lead">{t("upload.lead")}</p>
      <div
        className={`dropzone ${drag ? "drag" : ""}`}
        data-testid="dropzone"
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void handleFiles(e.dataTransfer.files);
        }}
      >
        <p>{t("upload.drop")}</p>
        <button className="btn btn-primary" disabled={busy || !!jobId} onClick={() => inputRef.current?.click()}>
          {t("upload.choose")}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          data-testid="file-input"
          accept={(meta?.formats || []).map((f) => `.${f}`).join(",")}
          onChange={(e) => {
            if (e.target.files) void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {meta && meta.examples.length > 0 && (
          <div className="examples">
            <span className="muted">{t("upload.try_example")}</span>
            {meta.examples.map((ex) => (
              <button key={ex.name} className="btn btn-ghost" disabled={busy || !!jobId} onClick={() => void tryExample(ex.name)} data-testid={`example-${ex.name}`}>
                {ex.name}
              </button>
            ))}
          </div>
        )}
      </div>
      {uploadPct !== null && (
        <div className="job">
          <div className="job-head">
            <strong>{t("jobs.stage.upload")}</strong>
            <span className="muted">{Math.round(uploadPct * 100)} %</span>
          </div>
          <div className="progress">
            <div className="progress-bar" style={{ width: `${uploadPct * 100}%` }} />
          </div>
          <button className="btn btn-ghost" onClick={() => void cancel()}>
            {t("common.cancel")}
          </button>
        </div>
      )}
      {job && <JobProgress job={job} onCancel={() => void cancel()} />}
      <ErrorBox error={error} onClose={() => setError(null)} />
      {failed.map((f) => (
        <FailedFile key={f.id} file={f} projectId={project!.id} onJob={setJobId} />
      ))}
      {project && project.tables.length > 0 && !jobId && (
        <button className="btn" onClick={onReady}>
          {t("upload.continue")}
        </button>
      )}
      <details className="info">
        <summary>{t("upload.formats_title")}</summary>
        <p>{t("upload.formats_supported", { formats: (meta?.formats || []).join(", ") })}</p>
        <p>{t("upload.formats_refused")}</p>
        {meta && <p>{t("upload.limits", meta.limits)}</p>}
        <p>{t("upload.privacy")}</p>
        {meta && !meta.ocr.available && <p className="warn">{t("upload.ocr_missing")}</p>}
      </details>
      {!project && projects.length > 0 && (
        <div className="projects">
          <h2>{t("projects.title")}</h2>
          <ul>
            {projects.map((p) => (
              <li key={p.id}>
                <button className="link" onClick={() => onProject(p.id)} data-testid="open-project">
                  {p.name}
                </button>
                <span className="muted"> · {new Date(p.updated_at * 1000).toLocaleString(i18n.language === "zh-Hans" ? "zh-CN" : i18n.language)}</span>
                <button
                  className="btn btn-ghost btn-small"
                  onClick={async () => {
                    if (!window.confirm(t("projects.confirm_delete", { name: p.name }))) return;
                    await send("DELETE", `/projects/${p.id}`).catch(setError);
                    setProjects(projects.filter((x) => x.id !== p.id));
                  }}
                >
                  {t("common.delete")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function FailedFile({ file, projectId, onJob }: { file: SourceFile; projectId: string; onJob: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const [pages, setPages] = useState("");
  const [delimiter, setDelimiter] = useState("");
  const needsPages = file.error?.code === "too_many_pages" || file.error?.code === "too_many_ocr_pages";
  return (
    <div className="alert alert-error">
      <div>
        <strong>{file.name}</strong> — {errorText(t, file.error)}
      </div>
      <div className="hint">{t(`next_action.${file.error?.code}`, { defaultValue: t("next_action.default") })}</div>
      {needsPages && (
        <div className="row">
          <input placeholder={t("upload.pages_placeholder")} value={pages} onChange={(e) => setPages(e.target.value)} />
          <button className="btn" onClick={async () => onJob((await send<{ job_id: string }>("POST", `/projects/${projectId}/files/${file.id}/reingest`, { options: { pages }, ui_lang: i18n.language })).job_id)}>
            {t("upload.retry_pages")}
          </button>
        </div>
      )}
      {["csv", "txt"].includes(file.ext) && (
        <div className="row">
          <select value={delimiter} onChange={(e) => setDelimiter(e.target.value)}>
            <option value="">{t("upload.delimiter_auto")}</option>
            <option value=",">,</option>
            <option value=";">;</option>
            <option value={"\t"}>TAB</option>
            <option value="|">|</option>
          </select>
          <button className="btn" onClick={async () => onJob((await send<{ job_id: string }>("POST", `/projects/${projectId}/files/${file.id}/reingest`, { options: delimiter ? { delimiter } : {}, ui_lang: i18n.language })).job_id)}>
            {t("upload.retry")}
          </button>
        </div>
      )}
    </div>
  );
}
