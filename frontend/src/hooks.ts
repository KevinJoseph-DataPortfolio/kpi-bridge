import { useEffect, useRef, useState } from "react";
import { get } from "./api";
import type { Job } from "./types";

// Suivi d'une tâche serveur ; la tâche continue côté serveur même si la page est rechargée.
export function useJob(jobId: string | null, onFinish?: (job: Job) => void): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  const cb = useRef(onFinish);
  cb.current = onFinish;
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const j = await get<Job>(`/jobs/${jobId}`);
        if (stop) return;
        setJob(j);
        if (["done", "failed", "cancelled"].includes(j.status)) {
          cb.current?.(j);
          return;
        }
      } catch {
        /* réessai */
      }
      timer = setTimeout(tick, 600);
    };
    void tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [jobId]);
  return job;
}
