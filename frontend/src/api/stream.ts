import { useEffect, useState } from "react";
import { BASE_URL } from "./client";

export interface StageEvent {
  stage: string;
  ticker: string;
  detail: Record<string, unknown>;
  /** Identitas run penerbit, agar sisa event run sebelumnya tidak terbaca sebagai progres. */
  run_id: string | null;
  created_at: string;
}

/**
 * Tahap pipeline langsung dari backend lewat SSE.
 *
 * Dipasang hanya selama run berjalan. Komponen pemakainya diberi `key` per run, jadi state-nya
 * mulai bersih tanpa perlu dikosongkan dari dalam effect.
 */
export function useRunStream(active: boolean) {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!active) return;
    const source = new EventSource(`${BASE_URL}/run/stream`);

    const onReady = () => setConnected(true);
    const onStage = (message: Event) => {
      try {
        setEvents((previous) => [...previous, JSON.parse((message as MessageEvent).data) as StageEvent]);
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
