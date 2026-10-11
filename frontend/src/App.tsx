import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { bootstrapSession, get, send } from "./api";
import { ErrorBox, LangSelect, Stepper } from "./components/common";
import Builder from "./screens/Builder";
import DashboardView from "./screens/DashboardView";
import DataFix from "./screens/DataFix";
import PlanReview from "./screens/PlanReview";
import Understand from "./screens/Understand";
import Upload from "./screens/Upload";
import type { Meta, Project } from "./types";

type View = "home" | "ask" | "plan" | "fix" | "builder" | "dashboard";
const PROJECT_KEY = "kpi-bridge.project";

function loadSaved(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

export default function App() {
  const { t } = useTranslation();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [projectId, setProjectId] = useState<string | null>(loadSaved());
  const [project, setProject] = useState<Project | null>(null);
  const [view, setView] = useState<View>("home");
  const [planId, setPlanId] = useState<string | null>(null);
  const [tableId, setTableId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    bootstrapSession()
      .then(setAuthed)
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (!authed) return;
    get<Meta>("/meta").then(setMeta).catch(setError);
  }, [authed]);

  const openProject = useCallback((id: string | null) => {
    setProjectId(id);
    try {
      if (id) localStorage.setItem(PROJECT_KEY, id);
      else localStorage.removeItem(PROJECT_KEY);
    } catch {
      /* ignore */
    }
    if (!id) {
      setProject(null);
      setView("home");
      setPlanId(null);
      setTableId(null);
    }
  }, []);

  const reload = useCallback(async (): Promise<Project | null> => {
    if (!projectId) return null;
    try {
      const p = await get<Project>(`/projects/${projectId}`);
      setProject(p);
      return p;
    } catch (e) {
      // projet supprimé ou introuvable : retour à l'accueil
      openProject(null);
      setError(e);
      return null;
    }
  }, [projectId, openProject]);

  // Reprise après rechargement : projet, tâche active, dernier plan.
  useEffect(() => {
    if (!authed || !projectId) return;
    void reload().then(async (p) => {
      if (!p) return;
      const lastPlan = p.state.last_plan_id as string | undefined;
      const lastTable = (p.state.last_table_id as string | undefined) ?? p.tables[0]?.id ?? null;
      setTableId(lastTable && p.tables.some((tb) => tb.id === lastTable) ? lastTable : p.tables[0]?.id ?? null);
      if (p.active_jobs.some((j) => j.kind === "ingest")) {
        setView("home");
      } else if (lastPlan && p.plans.some((pl) => pl.id === lastPlan)) {
        setPlanId(lastPlan);
        const st = p.plans.find((pl) => pl.id === lastPlan)?.status;
        setView(st === "validated" ? "dashboard" : "plan");
      } else if (p.tables.length) {
        setView("ask");
      } else {
        setView("home");
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, projectId]);

  const step = view === "home" ? 0 : view === "ask" || view === "builder" ? 1 : view === "plan" || view === "fix" ? 2 : 3;

  if (authed === null) return <div className="center muted">{t("common.loading")}</div>;
  if (!authed)
    return (
      <div className="center session-required" data-testid="session-required">
        <h1>KPI Bridge</h1>
        <p>{t("session.required")}</p>
        <code>launch.cmd / ./launch.sh</code>
      </div>
    );

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => openProject(null)} title={t("nav.home")}>
          <span className="logo">◧</span> KPI Bridge
        </button>
        {project && <span className="project-name" data-testid="project-name">{project.name}</span>}
        <div className="spacer" />
        {meta && !meta.ai.reachable && <span className="badge badge-warn" title={t("ai.unreachable_hint")}>{t("ai.unreachable")}</span>}
        {meta?.ai.test_double && <span className="badge badge-warn">{t("ai.test_double")}</span>}
        <LangSelect />
      </header>
      {project && <Stepper step={step} />}
      <main className="content">
        <ErrorBox error={error} onClose={() => setError(null)} />
        {view === "home" && (
          <Upload
            meta={meta}
            project={project}
            onProject={(id) => openProject(id)}
            onReady={async () => {
              const p = await reload();
              if (p?.tables.length) {
                setTableId(p.tables[0].id);
                setView("ask");
              }
            }}
            onReload={reload}
          />
        )}
        {view === "ask" && project && (
          <Understand
            project={project}
            meta={meta}
            tableId={tableId}
            onTable={setTableId}
            onReload={reload}
            onPlan={(id) => {
              setPlanId(id);
              setView("plan");
            }}
            onBuilder={() => setView("builder")}
            onFix={() => setView("fix")}
            onAddFile={() => setView("home")}
          />
        )}
        {view === "fix" && project && tableId && <DataFix project={project} tableId={tableId} onBack={() => (planId ? setView("plan") : setView("ask"))} onReload={reload} />}
        {view === "builder" && project && tableId && (
          <Builder
            project={project}
            tableId={tableId}
            replacePlan={null}
            onDone={(id) => {
              setPlanId(id);
              setView("plan");
            }}
            onBack={() => setView("ask")}
          />
        )}
        {view === "plan" && project && planId && (
          <PlanReview
            projectId={project.id}
            planId={planId}
            onValidated={() => setView("dashboard")}
            onModify={() => setView("ask")}
            onBuilder={() => setView("builder")}
            onFix={(tid) => {
              setTableId(tid);
              setView("fix");
            }}
          />
        )}
        {view === "dashboard" && project && planId && (
          <DashboardView
            project={project}
            planId={planId}
            onModify={() => setView("ask")}
            onInvalid={() => setView("plan")}
            onDeleted={async () => {
              await send("DELETE", `/projects/${project.id}`);
              openProject(null);
            }}
          />
        )}
      </main>
      <footer className="footer muted">
        {t("footer.local")} · v{meta?.version ?? "…"}
      </footer>
    </div>
  );
}
