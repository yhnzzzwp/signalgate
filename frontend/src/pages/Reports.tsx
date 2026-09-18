import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  cancelWorkflowRun,
  fetchActiveRun,
  fetchWorkflowRun,
  fetchWorkflowRuns,
  fetchRun,
  resumeWorkflowRun,
  startWorkflowRun,
} from "../api/client";
import { RunProgress } from "../components/RunProgress";
import { WorkflowPanels } from "../components/WorkflowPanels";
import type { RunJob, WorkflowReport, WorkflowRun } from "../types";

const RUN_STATUS_TEXT: Record<string, string> = {
  pending: "menunggu",
  running: "berjalan",
  completed: "selesai",
  partial: "sebagian",
  needs_review: "perlu pemeriksaan",
  failed: "gagal",
};

function describeRun(job: RunJob): string {
  if (job.status === "failed") return job.error ?? "Run gagal tanpa keterangan.";
  const result = job.result;
  if (!result?.run_id) return "Run selesai.";
  const panels = Object.entries(result.panels ?? {})
    .map(([domain, status]) => `${domain}: ${RUN_STATUS_TEXT[status] ?? status}`)
    .join(", ");
  const credits = result.credits_used ? `${result.credits_used} kredit Sectors` : "tanpa kredit Sectors";
  const unresolved = result.unresolved_claims
    ? `, ${result.unresolved_claims} klaim belum terverifikasi`
    : "";
  return `Laporan ${result.ticker} ${RUN_STATUS_TEXT[result.status ?? ""] ?? result.status} (${credits})${unresolved}. ${panels}.`;
}

export function Reports() {
  const [ticker, setTicker] = useState("");
  const [horizon, setHorizon] = useState("medium");
  const [asOf, setAsOf] = useState("");
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<WorkflowReport | null>(null);
  const [job, setJob] = useState<RunJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const running = job?.status === "running" ? job : null;

  const loadRuns = useCallback(async () => {
    try {
      const page = await fetchWorkflowRuns({ limit: 20 });
      setRuns(page.results);
      return page.results;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Daftar run gagal dimuat.");
      return [];
    }
  }, []);

  const openRun = useCallback(async (runId: string) => {
    setSelected(runId);
    setLoading(true);
    try {
      const detail = await fetchWorkflowRun(runId);
      setReport(detail.report);
      // Run gagal tidak boleh menampilkan laporan lama sebagai hasilnya.
      setError(detail.report ? null : detail.run.error ?? "Run ini belum menghasilkan laporan.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Laporan gagal dimuat.");
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRuns().then((results) => {
      if (results.length > 0) void openRun(results[0].run_id);
    });
  }, [loadRuns, openRun]);

  useEffect(() => {
    let cancelled = false;
    fetchActiveRun()
      .then((active) => {
        if (!cancelled && active?.kind === "workflow") setJob(active);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await fetchRun(running.id);
        if (latest.status === "running") return;
        setJob(latest);
        setNotice(describeRun(latest));
        if (latest.status === "failed") setError(latest.error);
        const results = await loadRuns();
        const finished = latest.run_id ?? results[0]?.run_id;
        if (finished) await openRun(finished);
      } catch {
        // Kegagalan polling tidak boleh menutupi run yang sedang berjalan.
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [running, loadRuns, openRun]);

  async function launch(body: { ticker?: string; horizon?: string; as_of?: string; replay_of?: string }) {
    setError(null);
    setNotice(null);
    try {
      setJob(await startWorkflowRun(body));
    } catch (err) {
      const conflict = err instanceof ApiError && err.status === 409;
      if (conflict || !(err instanceof ApiError)) {
        const active = await fetchActiveRun().catch(() => null);
        if (active) {
          setJob(active);
          if (conflict) setNotice("Run lain sedang berjalan; progresnya ditampilkan di bawah.");
          return;
        }
      }
      setError(err instanceof Error ? err.message : "Run tidak bisa dimulai.");
    }
  }

  async function resume(runId: string) {
    setError(null);
    try {
      setJob(await resumeWorkflowRun(runId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run tidak bisa dilanjutkan.");
    }
  }

  const replayable = runs.filter((run) => run.data_mode === "live" && run.report_version);

  return (
    <section className="dashboard__page reports">
      <header className="reports__header">
        <div>
          <h2>Laporan emiten empat panel</h2>
          <p className="dashboard__disclaimer">
            Fundamental, valuasi, technical, dan berita dari snapshot Sectors yang sama. Angka dihitung kode;
            model hanya menafsirkan klaim yang lolos pemeriksaan bukti. Bukan rekomendasi beli atau jual.
          </p>
        </div>
      </header>

      <form
        className="reports__form"
        onSubmit={(event) => {
          event.preventDefault();
          void launch({ ticker: ticker.toUpperCase(), horizon, as_of: asOf || undefined });
        }}
      >
        <label className="reports__field">
          <span>Ticker</span>
          <input
            value={ticker}
            onChange={(event) => setTicker(event.target.value.toUpperCase().slice(0, 4))}
            placeholder="LPKR"
            pattern="[A-Za-z0-9]{4}"
            required
          />
        </label>
        <label className="reports__field">
          <span>Horizon</span>
          <select value={horizon} onChange={(event) => setHorizon(event.target.value)}>
            <option value="short">pendek</option>
            <option value="medium">menengah</option>
            <option value="long">panjang</option>
          </select>
        </label>
        <label className="reports__field">
          <span>Tanggal acuan</span>
          <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
        </label>
        <button className="dashboard__run-button" type="submit" disabled={running !== null}>
          {running?.kind === "workflow" ? "Menyusun laporan…" : "Buat laporan (Sectors)"}
        </button>
        {replayable.length > 0 && (
          <button
            className="dashboard__filter"
            type="button"
            disabled={running !== null}
            onClick={() => void launch({ replay_of: replayable[0].run_id })}
            title={`Memutar ulang snapshot ${replayable[0].ticker} ${replayable[0].as_of} tanpa kredit`}
          >
            Putar ulang snapshot terakhir
          </button>
        )}
      </form>

      {error && (
        <div className="dashboard__error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="dashboard__notice" role="status">
          {notice}
        </div>
      )}
      {running && (
        <>
          <RunProgress key={running.id} job={running} />
          {running.run_id && (
            <button className="dashboard__filter" onClick={() => void cancelWorkflowRun(running.run_id!)}>
              Batalkan run
            </button>
          )}
        </>
      )}

      {runs.length > 0 && (
        <ul className="reports__runs">
          {runs.map((run) => (
            <li key={run.run_id}>
              <button
                className={`reports__run ${selected === run.run_id ? "reports__run--active" : ""}`}
                aria-pressed={selected === run.run_id}
                onClick={() => void openRun(run.run_id)}
              >
                <strong>{run.ticker}</strong>
                <span>{run.as_of}</span>
                <span className={`reports__badge reports__badge--${run.status}`}>
                  {RUN_STATUS_TEXT[run.status] ?? run.status}
                </span>
                <span className="event-card__source-meta">
                  {run.data_mode === "replay" ? "replay snapshot" : "data live"}
                </span>
              </button>
              {run.status === "failed" && (
                <button className="dashboard__filter" onClick={() => void resume(run.run_id)}>
                  Lanjutkan dari checkpoint
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {loading && <p className="dashboard__empty">Memuat laporan…</p>}
      {!loading && report && <WorkflowPanels report={report} />}
      {!loading && !report && runs.length === 0 && (
        <p className="dashboard__empty">
          Belum ada laporan. Isi ticker lalu jalankan; tiap run memakai sekitar 13 kredit Sectors.
        </p>
      )}
    </section>
  );
}
