import type { AuditLogEntry, ScreenedEventSummary } from "../types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    throw new Error(`SignalGate API ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchEvents(): Promise<ScreenedEventSummary[]> {
  return request("/events");
}

export function fetchAuditLog(): Promise<AuditLogEntry[]> {
  return request("/audit");
}

export function triggerPipelineRun(): Promise<{ screened_count: number; provider: string }> {
  return request("/pipeline/run", { method: "POST" });
}
