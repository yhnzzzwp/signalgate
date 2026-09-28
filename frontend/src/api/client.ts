import type {
  AuditLogEntry,
  EventPage,
  Page,
  RunJob,
  RuntimeCheck,
  RuntimeConfig,
  RuntimeFrontierMode,
  RuntimeTarget,
  WorkflowReport,
  WorkflowRun,
} from "../types";

export const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Kegagalan yang dijawab server, lengkap dengan penjelasannya.
 *
 * Dibedakan dari kegagalan jaringan karena artinya berbeda: server yang menolak berarti tidak ada
 * yang berjalan, sedangkan koneksi yang putus berarti hasilnya mungkin sudah tersimpan.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Isi `detail` terstruktur dari server (mis. daftar perubahan konfigurasi saat resume ditolak). */
  readonly detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function failure(response: Response, path: string): Promise<ApiError> {
  const fallback = `SignalGate API ${path} gagal dengan status ${response.status}.`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return new ApiError(body.detail, response.status);
    const message = (body.detail as { message?: unknown } | undefined)?.message;
    if (typeof message === "string") return new ApiError(message, response.status, body.detail);
  } catch {
    // Respons non-JSON (proxy, halaman error): pakai pesan umum di bawah.
  }
  return new ApiError(fallback, response.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    throw await failure(response, path);
  }
  return response.json() as Promise<T>;
}

export interface EventQuery {
  limit?: number;
  offset?: number;
  label?: string;
  ticker?: string;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function fetchEvents(options: EventQuery = {}): Promise<EventPage> {
  return request(`/events${query({ ...options })}`);
}

export function fetchAuditLog(
  options: { limit?: number; offset?: number; ticker?: string } = {},
): Promise<Page<AuditLogEntry>> {
  return request(`/audit${query({ ...options })}`);
}

// Run berjalan di latar: POST menjawab seketika dengan identitas job, bukan menunggu sampai selesai.
export function triggerPipelineRun(): Promise<RunJob> {
  return request("/pipeline/run", { method: "POST" });
}

export function triggerScanRun(limit = 3): Promise<RunJob> {
  return request(`/scan/run?limit=${limit}`, { method: "POST" });
}

export function triggerScanRetry(ticker: string): Promise<RunJob> {
  return request(`/scan/retry/${encodeURIComponent(ticker)}`, { method: "POST" });
}

/** Run yang sedang berjalan, dipakai dashboard untuk menyambung lagi setelah halaman dimuat ulang. */
export function fetchActiveRun(): Promise<RunJob | null> {
  return request("/runs/active");
}

export function fetchRun(jobId: string): Promise<RunJob> {
  return request(`/runs/${jobId}`);
}

/** Laporan empat panel. Mode replay memakai snapshot run lain, jadi tidak memakai kredit Sectors. */
export function startWorkflowRun(body: {
  ticker?: string;
  horizon?: string;
  as_of?: string;
  replay_of?: string;
}): Promise<RunJob> {
  return request("/workflow/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Resume ditolak (409, berisi daftar perubahan) bila konfigurasi runtime berubah, kecuali disetujui. */
export function resumeWorkflowRun(runId: string, acceptConfigChange = false): Promise<RunJob> {
  const suffix = acceptConfigChange ? "?accept_config_change=true" : "";
  return request(`/workflow/runs/${runId}/resume${suffix}`, { method: "POST" });
}

export function cancelWorkflowRun(runId: string): Promise<{ run_id: string; cancelling: boolean }> {
  return request(`/workflow/runs/${runId}/cancel`, { method: "POST" });
}

export function fetchWorkflowRuns(
  options: { ticker?: string; limit?: number } = {},
): Promise<{ results: WorkflowRun[]; stages: { key: string; label: string }[] }> {
  return request(`/workflow/runs${query({ ...options })}`);
}

export function fetchWorkflowRun(runId: string): Promise<{ run: WorkflowRun; report: WorkflowReport | null }> {
  return request(`/workflow/runs/${runId}`);
}

/** Pengaturan runtime: lokasi GPU dan mode frontier untuk run berikutnya. Token tidak pernah dikembalikan. */
export function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  return request("/runtime/config");
}

export interface RuntimeRequest {
  target: RuntimeTarget;
  colab_url?: string;
  /** Hanya dikirim bila diisi; kosong = pakai token tersimpan, hanya untuk endpoint asalnya. */
  token?: string;
  /** Persetujuan eksplisit memakai token tersimpan untuk host yang berbeda dari asalnya. */
  reuse_saved_token?: boolean;
}

function runtimeBody(body: object): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function checkRuntime(
  body: RuntimeRequest & { probe_gpu?: boolean; check_frontier_key?: boolean },
): Promise<RuntimeCheck> {
  return request("/runtime/check", runtimeBody(body));
}

export function activateRuntime(
  body: RuntimeRequest & { frontier_mode: RuntimeFrontierMode; allow_not_ready?: boolean },
): Promise<RuntimeConfig & { check: RuntimeCheck; saved_not_ready: boolean }> {
  return request("/runtime/activate", runtimeBody(body));
}
