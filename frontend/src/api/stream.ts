import { useEffect, useState } from "react";
import { BASE_URL } from "./client";

export interface StageEvent {
  stage: string;
  ticker: string;
  detail: Record<string, unknown>;
  /** Identitas run penerbit, agar sisa event run sebelumnya tidak terbaca sebagai progres. */
  run_id: string | null;
  /** Nomor urut dari backend; riwayat yang diputar ulang saat menyambung tidak boleh tercatat dua kali. */
  seq?: number;
  created_at: string;
}

/**
 * Tahap pipeline langsung dari backend lewat SSE.
 *
 * Dipasang hanya selama run berjalan. Komponen pemakainya diberi `key` per run, jadi state-nya
 * mulai bersih tanpa perlu dikosongkan dari dalam effect. Backend memutar ulang riwayat run aktif
 * untuk koneksi baru, jadi refresh halaman memulihkan timeline dan hitungan kasus.
 */
export function useRunStream(active: boolean) {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!active) return;
    const source = new EventSource(`${BASE_URL}/run/stream`);
    let lastSeq = 0;

    const onReady = () => setConnected(true);
    const onStage = (message: Event) => {
      try {
        const event = JSON.parse((message as MessageEvent).data) as StageEvent;
        if (typeof event.seq === "number") {
          if (event.seq <= lastSeq) return;
          lastSeq = event.seq;
        }
        setEvents((previous) => [...previous, event]);
      } catch {
        // Pesan rusak dilewati; satu baris hilang tidak sebanding dengan menjatuhkan tampilan.
      }
    };
    // EventSource menyambung ulang sendiri, jadi error hanya menandai koneksi sedang putus.
    const onError = () => setConnected(false);

    source.addEventListener("ready", onReady);
    source.addEventListener("stage", onStage);
    source.addEventListener("heartbeat", onReady);
    source.addEventListener("error", onError);

    return () => {
      source.removeEventListener("ready", onReady);
      source.removeEventListener("stage", onStage);
      source.removeEventListener("heartbeat", onReady);
      source.removeEventListener("error", onError);
      source.close();
    };
  }, [active]);

  return { events, connected };
}
