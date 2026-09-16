import { useEffect, useState } from "react";
import { fetchAuditLog, fetchEvents, triggerPipelineRun, triggerScanRun } from "../api/client";
import { AuditTrail } from "../components/AuditTrail";
import { EventCard } from "../components/EventCard";
import type { AuditLogEntry, ScreenedEventSummary, VerdictLabel } from "../types";

type LabelFilter = VerdictLabel | "all";

export function Dashboard() {
  const [events, setEvents] = useState<ScreenedEventSummary[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>([]);
  const [filter, setFilter] = useState<LabelFilter>("all");
  const [showAudit, setShowAudit] = useState(false);
  const [running, setRunning] = useState<"pipeline" | "scan" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
    setRunning("pipeline");
    setNotice(null);
    try {
      await triggerPipelineRun();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pipeline run gagal.");
    } finally {
      setRunning(null);
    }
  }

  async function handleRunScan() {
    setRunning("scan");
    setNotice(null);
    setError(null);
    try {
      const result = await triggerScanRun();
      await loadData();
      setNotice(
        result.screened_count > 0
          ? `${result.screened_count} kasus diriset dari ${result.candidates_found} kandidat. ${result.coverage_note}`
          : `Tidak ada kasus yang bisa diriset: ${result.candidates_found} kandidat ditemukan dari ` +
            `${result.articles_checked} artikel, tetapi tidak ada yang punya dokumen PDF. ${result.coverage_note}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan gagal.");
    } finally {
      setRunning(null);
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
            Insight dari Sectors API dan sumber web, dianalisis dan divalidasi model lokal — bukan rekomendasi beli/jual.
          </p>
        </div>
        <div className="dashboard__actions">
          <button className="dashboard__run-button" onClick={handleRunScan} disabled={running !== null}>
            {running === "scan" ? "Memindai sumber publik…" : "Scan Scrapling (tanpa API)"}
          </button>
          <button className="dashboard__run-button" onClick={handleRunPipeline} disabled={running !== null}>
            {running === "pipeline" ? "Menjalankan pipeline…" : "Jalankan pipeline (Sectors)"}
          </button>
        </div>
      </header>

      {error && <div className="dashboard__error">{error}</div>}
      {notice && <div className="dashboard__notice">{notice}</div>}

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
          {filteredEvents.length === 0 && (
            <p className="dashboard__empty">
              Belum ada event — jalankan &ldquo;Scan Scrapling&rdquo; untuk memulai tanpa memakai kredit API.
            </p>
          )}
          {filteredEvents.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}
