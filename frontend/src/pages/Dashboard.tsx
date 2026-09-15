import { useEffect, useState } from "react";
import { fetchAuditLog, fetchEvents, triggerPipelineRun } from "../api/client";
import { AuditTrail } from "../components/AuditTrail";
import { EventCard } from "../components/EventCard";
import type { AuditLogEntry, ScreenedEventSummary, VerdictLabel } from "../types";

type LabelFilter = VerdictLabel | "all";

export function Dashboard() {
  const [events, setEvents] = useState<ScreenedEventSummary[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>([]);
  const [filter, setFilter] = useState<LabelFilter>("all");
  const [showAudit, setShowAudit] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadData() {
    try {
      const [eventsResponse, auditResponse] = await Promise.all([fetchEvents(), fetchAuditLog()]);
      setEvents(eventsResponse);
      setAuditEntries(auditResponse);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat data.");
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleRunPipeline() {
    setIsRunning(true);
    try {
      await triggerPipelineRun();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pipeline run gagal.");
    } finally {
      setIsRunning(false);
    }
  }

  const filteredEvents = filter === "all" ? events : events.filter((event) => event.label === filter);
  const counts = events.reduce<Record<string, number>>((acc, event) => {
    acc[event.label] = (acc[event.label] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <div>
          <h1>SignalGate</h1>
          <p className="dashboard__disclaimer">
            Murni insight screening berbasis Sectors API — bukan rekomendasi beli/jual.
          </p>
        </div>
        <button className="dashboard__run-button" onClick={handleRunPipeline} disabled={isRunning}>
          {isRunning ? "Menjalankan pipeline…" : "Jalankan pipeline"}
        </button>
      </header>

      {error && <div className="dashboard__error">{error}</div>}

      <div className="dashboard__filters">
        {(["all", "structural_red_flag", "growth_catalyst", "inconclusive"] as LabelFilter[]).map((option) => (
          <button
            key={option}
            className={`dashboard__filter ${filter === option ? "dashboard__filter--active" : ""}`}
            onClick={() => setFilter(option)}
          >
            {option === "all" ? "Semua" : option.replace("_", " ")}
            <span className="dashboard__filter-count">{option === "all" ? events.length : counts[option] ?? 0}</span>
          </button>
        ))}
        <button className="dashboard__filter dashboard__filter--audit" onClick={() => setShowAudit((prev) => !prev)}>
          {showAudit ? "Sembunyikan audit trail" : "Lihat audit trail"}
        </button>
      </div>

      {showAudit ? (
        <AuditTrail entries={auditEntries} />
      ) : (
        <div className="dashboard__grid">
          {filteredEvents.length === 0 && <p className="dashboard__empty">Belum ada event — jalankan pipeline dulu.</p>}
          {filteredEvents.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}
