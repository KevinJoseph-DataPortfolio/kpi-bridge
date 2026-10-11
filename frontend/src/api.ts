// Client HTTP : même origine, cookie HttpOnly, en-tête anti-CSRF sur toute requête modifiante.

export class ApiError extends Error {
  code: string;
  params: Record<string, unknown>;
  status: number;
  constructor(status: number, code: string, params: Record<string, unknown> = {}) {
    super(code);
    this.status = status;
    this.code = code;
    this.params = params;
  }
}

const BASE = "/api/v1";

async function parse(res: Response): Promise<unknown> {
  const type = res.headers.get("content-type") || "";
  if (!res.ok) {
    if (type.includes("application/json")) {
      const body = (await res.json()) as { error?: { code?: string; params?: Record<string, unknown> } };
      throw new ApiError(res.status, body.error?.code || "http_error", body.error?.params || {});
    }
    throw new ApiError(res.status, "http_error");
  }
  if (type.includes("application/json")) return res.json();
  return res.blob();
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path, { credentials: "same-origin" });
  return (await parse(res)) as T;
}

export async function send<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-KPI-CSRF": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await parse(res)) as T;
}

export async function download(path: string, body: unknown): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(BASE + path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-KPI-CSRF": "1" },
    body: JSON.stringify(body),
  });
  if (!res.ok) await parse(res);
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename="([^"]+)"/.exec(cd);
  return { blob: await res.blob(), filename: m ? m[1] : "export" };
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// Envoi de fichier en flux brut (pas de multipart) avec progression réelle d'envoi.
export function uploadFile(
  projectId: string,
  file: File,
  lang: string,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<{ file: { id: string; name: string }; job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `${BASE}/projects/${projectId}/files`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-KPI-CSRF", "1");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.setRequestHeader("X-UI-Lang", lang);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText || "{}");
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new ApiError(xhr.status, body.error?.code || "http_error", body.error?.params || {}));
      } catch {
        reject(new ApiError(xhr.status, "http_error"));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error"));
    xhr.onabort = () => reject(new ApiError(0, "upload_aborted"));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

export async function bootstrapSession(): Promise<boolean> {
  const hash = window.location.hash;
  const m = /token=([A-Za-z0-9_-]{20,200})/.exec(hash);
  if (m) {
    // Le secret à usage unique est retiré de l'URL et de l'historique immédiatement.
    history.replaceState(null, "", window.location.pathname);
    try {
      await send("POST", "/session/bootstrap", { token: m[1] });
    } catch {
      /* jeton expiré ou déjà utilisé : l'état de session est vérifié ci-dessous */
    }
  }
  const s = await get<{ authenticated: boolean }>("/session");
  return s.authenticated;
}
