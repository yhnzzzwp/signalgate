"""Fan-out tahap pipeline ke klien SSE yang sedang terhubung.

Dashboard dulu polling /audit tiap beberapa detik. Satu run memakan menit dan menulis tahapnya
seiring jalan, jadi mendorongnya begitu terjadi membuat progres terasa langsung tanpa membebani
database dengan permintaan berulang.

Sengaja in-process dan tanpa ketergantungan: satu backend melayani satu run pada satu waktu
(dijaga `run_lock`), jadi broker pesan tidak akan membeli apa pun di sini.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import datetime, timezone

# Sinyal hidup dikirim saat sepi supaya proxy tidak memutus koneksi yang diam.
HEARTBEAT_SECONDS = 15.0
# Generator ini async supaya tidak menahan thread dari threadpool: endpoint sinkron akan memegang
# satu thread per klien hingga IDLE_TIMEOUT_SECONDS, dan /scan/run juga sinkron serta berjalan
# menit-menitan. Antreannya dicek tanpa memblokir, jadi jeda inilah latensi tampilannya.
POLL_SECONDS = 0.25
# Katup pengaman: klien yang ditinggalkan tidak boleh menahan thread selamanya. EventSource di
# browser menyambung ulang sendiri, jadi menutup koneksi yang menganggur tidak terlihat pengguna.
IDLE_TIMEOUT_SECONDS = 600.0
QUEUE_LIMIT = 1000
# Riwayat run yang sedang berjalan, diputar ulang untuk klien yang baru tersambung. Satu kasus
# menerbitkan belasan event, jadi batas ini menampung run dengan limit maksimum (20 kasus).
HISTORY_LIMIT = 600


class RunEventStream:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        # Satu run pada satu waktu (dijaga run_lock), jadi id-nya cukup disimpan di sini dan ikut
        # pada setiap payload. Klien memakainya untuk membuang sisa event run sebelumnya.
        self.run_id: str | None = None
        # Event dulu hanya dikirim ke pelanggan yang sedang tersambung, sehingga refresh halaman di
        # tengah run menampilkan "Menyiapkan run…" sampai event berikutnya, padahal model sedang
        # membaca. Nomor urut membuat penyambungan ulang tidak menerima event yang sama dua kali.
        self._history: deque[dict] = deque(maxlen=HISTORY_LIMIT)
        self._sequence = 0

    def set_run(self, run_id: str | None) -> None:
        with self._lock:
            if run_id is not None and run_id != self.run_id:
                self._history.clear()
            self.run_id = run_id

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, stage: str, ticker: str, detail: dict | None = None) -> None:
        with self._lock:
            self._sequence += 1
            payload = {
                "stage": stage,
                "ticker": ticker,
                "detail": detail or {},
                "run_id": self.run_id,
                "seq": self._sequence,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if self.run_id is not None:
                self._history.append(payload)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                pass  # Klien lambat kehilangan tahap, bukan menghentikan run.

    @contextmanager
    def subscribe(self, after: int | None = None):
        """Daftarkan pelanggan; riwayat run aktif setelah `after` masuk antreannya lebih dulu.

        Riwayat dan pendaftaran terjadi di bawah kunci yang sama dengan publish, jadi tidak ada
        event yang terlewat maupun terkirim dua kali di antara keduanya. Nomor dari proses backend
        sebelumnya (lebih besar dari urutan sekarang) diabaikan dan riwayat dikirim utuh.
        """
        channel: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        with self._lock:
            if self.run_id is not None:
                start = after if after is not None and after <= self._sequence else 0
                for payload in self._history:
                    if payload["seq"] <= start:
                        continue
                    try:
                        channel.put_nowait(payload)
                    except queue.Full:
                        break
            self._subscribers.add(channel)
        try:
            yield channel
        finally:
            with self._lock:
                self._subscribers.discard(channel)


run_events = RunEventStream()


def sse(event: str, data: dict) -> str:
    # `id:` membuat EventSource mengirim Last-Event-ID saat menyambung ulang.
    identity = f"id: {data['seq']}\n" if isinstance(data.get("seq"), int) else ""
    return f"{identity}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_source(stream: RunEventStream | None = None,
                       last_event_id: int | None = None) -> AsyncIterator[str]:
    """Generator SSE: riwayat run aktif, lalu satu pesan per tahap, plus detak jantung saat sepi."""
    stream = stream or run_events
    with stream.subscribe(after=last_event_id) as channel:
        yield sse("ready", {"listening": True})
        idle = since_heartbeat = 0.0
        while idle < IDLE_TIMEOUT_SECONDS:
            try:
                payload = channel.get_nowait()
            except queue.Empty:
                await asyncio.sleep(POLL_SECONDS)
                idle += POLL_SECONDS
                since_heartbeat += POLL_SECONDS
                if since_heartbeat >= HEARTBEAT_SECONDS:
                    since_heartbeat = 0.0
                    yield sse("heartbeat", {"idle_seconds": round(idle, 2)})
                continue
            idle = since_heartbeat = 0.0
            yield sse("stage", payload)
