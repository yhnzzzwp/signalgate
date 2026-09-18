import type {
  AuditLogEntry,
  EventPage,
  Page,
  RunJob,
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

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function failureMessage(response: Response, path: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // Respons non-JSON (proxy, halaman error): pakai pesan umum di bawah.
  }
  return `SignalGate API ${path} gagal dengan status ${response.status}.`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    throw new ApiError(await failureMessage(response, path), response.status);
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

export function resumeWorkflowRun(runId: string): Promise<RunJob> {
  return request(`/workflow/runs/${runId}/resume`, { method: "POST" });
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
