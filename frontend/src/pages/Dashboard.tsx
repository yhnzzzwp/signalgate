import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchActiveRun,
  fetchAuditLog,
  fetchEvents,
  fetchRun,
  triggerPipelineRun,
  triggerScanRun,
} from "../api/client";
import { AuditTrail } from "../components/AuditTrail";
import { EventCard } from "../components/EventCard";
import { RunProgress } from "../components/RunProgress";
import { LABEL_TEXT } from "../labels";
import type { AuditLogEntry, RunJob, ScanRunResult, ScreenedEventSummary, VerdictLabel } from "../types";

type LabelFilter = VerdictLabel | "all";
type LoadState = "loading" | "ready" | "error";

const PAGE_SIZE = 24;
const AUDIT_PAGE_SIZE = 50;
const ACTIVE_RUN_RETRY_MS = 5000;

/** Baris baru bisa masuk di atas selama pengguna membuka halaman berikutnya; yang sudah tampil dilewati. */
function appendUnique<T extends { id: number }>(previous: T[], next: T[]): T[] {
  const seen = new Set(previous.map((item) => item.id));
  return [...previous, ...next.filter((item) => !seen.has(item.id))];
}

function clockText(iso: string): string {
  return new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", hour12: false });
}
const LABELS: LabelFilter[] = ["all", "structural_red_flag", "growth_catalyst", "inconclusive"];

/**
 * "Nol hasil" punya sebab yang sangat berbeda, dan ringkasan harus menyebut yang mana.
 *
 * Endpoint meriset seluruh antrean tersimpan sementara jumlah kandidat berasal dari discovery kali
 * ini, jadi nol hasil sering berarti "semuanya memang sudah pernah diproses", bukan kegagalan.
 */
function describeScan(result: ScanRunResult): string {
  const parts: string[] = [];
  if (result.processed > 0) {
    parts.push(`${result.processed} kasus diriset.`);
  } else if (result.already_processed > 0 && result.new === 0) {
    parts.push(`Tidak ada kandidat baru; ${result.already_processed} temuan sudah pernah diproses.`);
  } else if (result.discovered === 0) {
    parts.push(
      `Tidak ada aksi korporasi yang cocok pada ${result.listing_pages_fetched} halaman sumber ` +
        `(${result.articles_checked} artikel diperiksa).`,
    );
  } else {
    parts.push(`${result.discovered} kandidat ditemukan, tetapi belum ada yang bisa diriset.`);
  }

  if (result.new > 0) parts.push(`${result.new} kandidat baru.`);
  if (result.ambiguous_documents > 0) {
    parts.push(`${result.ambiguous_documents} kandidat punya lampiran ambigu dan menunggu pemeriksaan.`);
  }
  if (result.pending > 0) parts.push(`${result.pending} masih mengantre.`);
  if (result.retry_waiting) {
    const when = result.next_retry_at ? ` paling cepat pukul ${clockText(result.next_retry_at)}` : "";
    parts.push(`${result.retry_waiting} kandidat gagal menunggu jadwal coba ulang${when}.`);
  }
  if (result.retry_exhausted) {
    parts.push(`${result.retry_exhausted} kandidat berhenti dicoba setelah beberapa kali gagal.`);
  }
  // Cakupan parsial tetap dilaporkan walau ada hasil.
  if (result.failed_sources?.length) {
    parts.push(`Sumber gagal diambil: ${result.failed_sources.map((item) => item.url).join(", ")}.`);
  }
  parts.push(result.coverage_note);
  return parts.filter(Boolean).join(" ");
}

function describeRun(job: RunJob): string {
  if (job.status === "failed") return job.error ?? "Run gagal tanpa keterangan.";
  if (!job.result) return "Run selesai.";
  if (job.kind === "scan") return describeScan(job.result as ScanRunResult);
  const researched = `${job.result.screened_count ?? 0} kasus diriset lewat Sectors.`;
  const skipped = job.result.already_processed ?? 0;
  return skipped > 0 ? `${researched} ${skipped} berita sudah pernah diputus dan dilewati tanpa memakai kredit.` : researched;
}

/**
 * Server yang menolak dan koneksi yang putus perlu dijawab berbeda.
 *
 * Server menjawab berarti run tidak pernah mulai, jadi penjelasannya ditampilkan apa adanya.
 * Koneksi putus berarti hasilnya mungkin sudah tersimpan, jadi daftarnya dimuat ulang lebih dulu.
 */
async function describeFailure(error: unknown, reload: () => Promise<void>): Promise<string> {
  if (error instanceof ApiError) return error.message;
  await reload();
  const reason = error instanceof Error ? error.message : "Koneksi ke backend terputus.";
  return `${reason} — cek daftar di bawah, hasilnya mungkin sudah tersimpan.`;
}

export function Dashboard() {
  const [events, setEvents] = useState<ScreenedEventSummary[]>([]);
  const [counts, setCounts] = useState<Partial<Record<VerdictLabel, number>>>({});
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditHasMore, setAuditHasMore] = useState(false);
  const [filter, setFilter] = useState<LabelFilter>("all");
  const [showAudit, setShowAudit] = useState(false);
  const [state, setState] = useState<LoadState>("loading");
  const [job, setJob] = useState<RunJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const running = job?.status === "running" ? job : null;

  const loadData = useCallback(async (nextOffset: number, label: LabelFilter) => {
    try {
      const [page, audit] = await Promise.all([
        fetchEvents({ limit: PAGE_SIZE, offset: nextOffset, label: label === "all" ? undefined : label }),
        // Jejak audit punya halamannya sendiri; memuat kartu lebih lama tidak boleh mengulang audit.
        nextOffset === 0 ? fetchAuditLog({ limit: AUDIT_PAGE_SIZE }) : Promise.resolve(null),
      ]);
      setEvents((previous) => (nextOffset === 0 ? page.results : appendUnique(previous, page.results)));
      setCounts(page.counts);
      setTotal(page.total);
      setHasMore(page.has_more);
      setOffset(nextOffset);
      if (audit) {
        setAuditEntries(audit.results);
        setAuditTotal(audit.total);
        setAuditHasMore(audit.has_more);
      }
      setError(null);
      setState("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat data.");
      setState("error");
    }
  }, []);

  useEffect(() => {
    void loadData(0, filter);
  }, [filter, loadData]);

  // Sambung kembali ke run yang masih berjalan: refresh halaman tidak boleh menghilangkan progres.
  // Backend yang belum siap saat halaman dibuka dicoba lagi, bukan dianggap "tidak ada run".
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const attempt = () => {
      fetchActiveRun()
        .then((active) => {
          if (!cancelled && active) setJob(active);
        })
        .catch(() => {
          if (!cancelled) timer = window.setTimeout(attempt, ACTIVE_RUN_RETRY_MS);
        });
    };
    attempt();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  // Ikuti job sampai ditutup lalu muat hasilnya. SSE memberi detail per operasi; ini penutupnya.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await fetchRun(running.id);
        if (latest.status === "running") return;
        setJob(latest);
        setNotice(describeRun(latest));
        if (latest.status === "failed") setError(latest.error);
        await loadData(0, filter);
      } catch {
        // Kegagalan polling tidak boleh menutupi run yang sedang berjalan.
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [running, filter, loadData]);

  async function startRun(kind: "scan" | "pipeline") {
    setNotice(null);
    setError(null);
    try {
      setJob(kind === "scan" ? await triggerScanRun() : await triggerPipelineRun());
    } catch (err) {
      // 409 berarti ada run lain; koneksi putus bisa berarti run INI sudah dibuat. Keduanya diikuti,
      // bukan dijawab dengan pesan yang membuat tombol tampak bisa ditekan lagi.
      const conflict = err instanceof ApiError && err.status === 409;
      if (conflict || !(err instanceof ApiError)) {
        const active = await fetchActiveRun().catch(() => null);
        if (active) {
          setJob(active);
          if (conflict) setNotice("Run lain sedang berjalan; progresnya ditampilkan di bawah.");
          return;
        }
      }
      setError(await describeFailure(err, () => loadData(0, filter)));
    }
  }

  async function loadMoreAudit() {
    try {
      const page = await fetchAuditLog({ limit: AUDIT_PAGE_SIZE, offset: auditEntries.length });
      setAuditEntries((previous) => appendUnique(previous, page.results));
      setAuditTotal(page.total);
      setAuditHasMore(page.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Jejak audit lama gagal dimuat.");
    }
  }

  const emptyMessage =
    state === "loading"
      ? "Memuat…"
      : state === "error"
        ? "Data tidak bisa dimuat. Periksa apakah backend berjalan."
        : filter !== "all"
          ? `Tidak ada hasil berlabel ${LABEL_TEXT[filter]}. Coba tab Semua.`
          : "Belum ada event — jalankan “Scan Scrapling” untuk memulai tanpa memakai kredit API.";

  return (
    <div className="dashboard__page">
      <header className="dashboard__header">
        <div>
          <h1>SignalGate</h1>
          <p className="dashboard__disclaimer">
            Penyaringan aksi korporasi IDX dari sumber publik dan Sectors API, dibaca dan diperiksa silang oleh
            model lokal. Hasilnya screening berbukti, bukan rekomendasi beli atau jual.
          </p>
        </div>
        <div className="dashboard__actions">
          <button className="dashboard__run-button" onClick={() => startRun("scan")} disabled={running !== null}>
            {running?.kind === "scan" ? "Memindai sumber publik…" : "Scan Scrapling (tanpa API)"}
          </button>
          <button className="dashboard__run-button" onClick={() => startRun("pipeline")} disabled={running !== null}>
            {running?.kind === "pipeline" ? "Menjalankan pipeline…" : "Jalankan pipeline (Sectors)"}
          </button>
        </div>
      </header>

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
      {running && <RunProgress key={running.id} job={running} />}

      <div className="dashboard__filters">
        {LABELS.map((option) => (
          <button
            key={option}
            aria-pressed={filter === option}
            className={`dashboard__filter ${filter === option ? "dashboard__filter--active" : ""}`}
            onClick={() => setFilter(option)}
          >
            {option === "all" ? "Semua" : LABEL_TEXT[option]}
            <span className="dashboard__filter-count">
              {option === "all"
                ? Object.values(counts).reduce((sum, value) => sum + (value ?? 0), 0)
                : (counts[option] ?? 0)}
            </span>
          </button>
        ))}
        <button
          aria-expanded={showAudit}
          className="dashboard__filter dashboard__filter--audit"
          onClick={() => setShowAudit((previous) => !previous)}
        >
          {showAudit ? "Sembunyikan jejak audit" : "Lihat jejak audit"}
        </button>
      </div>

      {showAudit ? (
        <AuditTrail
          entries={auditEntries}
          total={auditTotal}
          hasMore={auditHasMore}
          onLoadMore={() => void loadMoreAudit()}
        />
      ) : (
        <>
          <div className="dashboard__grid">
            {events.length === 0 && <p className="dashboard__empty">{emptyMessage}</p>}
            {events.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
          {events.length > 0 && (
            <footer className="dashboard__paging">
              <span>
                Menampilkan {events.length} dari {total} hasil
                {filter !== "all" && ` berlabel ${LABEL_TEXT[filter]}`}
              </span>
              {hasMore && (
                <button className="dashboard__filter" onClick={() => void loadData(offset + PAGE_SIZE, filter)}>
                  Muat lebih lama
                </button>
              )}
            </footer>
          )}
        </>
      )}
    </div>
  );
}
