import { useEffect, useState } from "react";
import { useRunStream, type StageEvent } from "../api/stream";
import type { RunJob } from "../types";

/**
 * Tahap yang berlaku per jalur. Menampilkan tahap yang tidak pernah dijalankan membuat run scan
 * terlihat melewati "kumpulkan kandidat", dan menandainya selesai hanya karena indeksnya lebih
 * rendah adalah klaim yang tidak pernah terjadi.
 */
const STAGES: Record<RunJob["kind"], { key: string; label: string }[]> = {
  scan: [
    { key: "scan", label: "Memindai sumber publik" },
    { key: "model", label: "Model membaca bukti" },
    { key: "gate", label: "Pemeriksaan kepatuhan" },
  ],
  pipeline: [
    { key: "sense", label: "Mengumpulkan kandidat" },
    { key: "model", label: "Model membaca bukti" },
    { key: "validate", label: "Validasi silang" },
    { key: "gate", label: "Pemeriksaan kepatuhan" },
  ],
};

const ROLE_TEXT: Record<string, string> = {
  analyst: "analis",
  reviewer_1: "pembanding 1",
  reviewer_2: "pembanding 2",
  reviewer_3: "pembanding 3",
};

function elapsedText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes}m ${String(seconds % 60).padStart(2, "0")}s` : `${seconds}s`;
}

/** Kalimat status diambil dari operasi yang SEDANG berjalan, bukan tahap tertinggi sepanjang run. */
function activeLine(events: StageEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const detail = event.detail ?? {};
    if (event.stage === "model") {
      const role = ROLE_TEXT[String(detail.role)] ?? String(detail.role ?? "model");
      const model = String(detail.model ?? "").replace(/^ollama:/, "");
      if (detail.phase === "start") return `${event.ticker}: ${role} (${model}) sedang membaca bukti…`;
      return `${event.ticker}: ${role} selesai dalam ${detail.seconds}s`;
    }
    if (event.stage === "case" && detail.phase === "start") {
      return `Menyiapkan ${event.ticker} (kasus ${detail.index} dari ${detail.total})…`;
    }
    if (event.stage === "scan") return "Memindai sumber publik…";
    if (event.stage === "gate") return `${event.ticker}: pemeriksaan kepatuhan…`;
  }
  return "Menyiapkan run…";
}

function summarise(event: StageEvent): string {
  const detail = event.detail ?? {};
  switch (event.stage) {
    case "scan":
      return `${detail.candidates ?? 0} kandidat dari ${detail.articles_checked ?? 0} artikel`;
    case "model":
      return detail.phase === "start"
        ? `${ROLE_TEXT[String(detail.role)] ?? detail.role} mulai membaca`
        : `${ROLE_TEXT[String(detail.role)] ?? detail.role} selesai · ${detail.seconds}s`;
    case "case":
      return detail.phase === "start"
        ? `kasus ${detail.index}/${detail.total}`
        : `kartu tersimpan · ${detail.label}`;
    case "gate": {
      const terms = (detail.rejected_terms as string[] | undefined) ?? [];
      return terms.length > 0 ? `ditahan: ${terms.join(", ")}` : String(detail.status ?? "lolos");
    }
    case "research":
      return String(detail.status ?? "");
    case "sense":
      return `${(detail.selected as string[] | undefined)?.length ?? 0} event dipilih`;
    default:
      return "";
  }
}

export function RunProgress({ job }: { job: RunJob }) {
  const { events, connected } = useRunStream(true);
  const [elapsed, setElapsed] = useState(0);
  const startedAt = new Date(job.started_at).getTime();

  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  // Event dari run lain dibuang, jadi sisa run sebelumnya tidak terbaca sebagai progres.
  const mine = events.filter((event) => !event.run_id || event.run_id === job.id);
  const stages = STAGES[job.kind];
  const reached = new Set(mine.map((event) => event.stage));
  const latest = [...mine].reverse().find((event) => reached.has(event.stage) && stages.some((s) => s.key === event.stage));
  const activeIndex = stages.findIndex((stage) => stage.key === latest?.stage);
  // Kasus dihitung selesai saat kartunya tersimpan, bukan saat gate lewat.
  const finished = mine.filter((event) => event.stage === "case" && event.detail?.phase === "end").length;
  const caseTotal = mine.find((event) => event.stage === "case")?.detail?.total;

  return (
    <section className="run-progress" aria-live="polite">
      <header className="run-progress__header">
        <span className="run-progress__spinner" aria-hidden="true" />
        <div className="run-progress__headline">
          <strong>{activeLine(mine)}</strong>
          <p className="run-progress__meta">
            {elapsedText(elapsed)}
            {typeof caseTotal === "number" && ` · ${finished} dari ${caseTotal} kasus tersimpan`}
            <span className={`run-progress__link run-progress__link--${connected ? "on" : "off"}`}>
              {connected ? "tersambung langsung" : "menyambung ulang…"}
            </span>
          </p>
        </div>
      </header>

      <ol className="run-progress__stages">
        {stages.map((stage, index) => {
          const state =
            activeIndex < 0
              ? "pending"
              : index === activeIndex
                ? "active"
                : index < activeIndex && reached.has(stage.key)
                  ? "done"
                  : "pending";
          return (
            <li key={stage.key} className={`run-progress__stage run-progress__stage--${state}`}>
              <span className="run-progress__dot" aria-hidden="true" />
              {stage.label}
            </li>
          );
        })}
      </ol>

      {mine.length > 0 && (
        <ul className="run-timeline">
          {mine
            .slice(-14)
            .reverse()
            .map((event, index) => (
              <li key={`${event.created_at}-${index}`} className="run-timeline__row">
                <time className="run-timeline__time">
                  {new Date(event.created_at).toLocaleTimeString("id-ID", { hour12: false })}
                </time>
                <span className="run-timeline__ticker">{event.ticker === "*" ? "—" : event.ticker}</span>
                <span className="run-timeline__stage">
                  {stages.find((stage) => stage.key === event.stage)?.label ?? event.stage}
                </span>
                <span className="run-timeline__detail">{summarise(event)}</span>
              </li>
            ))}
        </ul>
      )}

      <p className="run-progress__note">
        Model lokal membaca tiap dokumen secara bergiliran, jadi satu kasus memakan beberapa menit.
        Halaman boleh ditutup atau dimuat ulang — run ini tetap berjalan dan akan tersambung kembali.
      </p>
    </section>
  );
}
