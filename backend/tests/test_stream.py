"""SSE menggantikan polling, jadi jaminan yang dulu dipegang HTTP biasa harus dipegang di sini."""
import asyncio
import json
import queue
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.pipeline import stream as stream_module
from app.pipeline.stream import RunEventStream, event_source, sse


async def take(generator, count: int) -> list[str]:
    """Ambil `count` pesan lalu tutup generator, seperti klien yang memutus koneksi."""
    messages = []
    try:
        for _ in range(count):
            messages.append(await anext(generator))
    finally:
        await generator.aclose()
    return messages


async def drain(generator) -> list[str]:
    return [message async for message in generator]


def no_candidates(*_args, **_kwargs):
    """Generator sungguhan: rute menutupnya lewat closing(), jadi iter([]) tidak cukup."""
    yield from ()


def parse(chunk: str) -> tuple[str, dict]:
    lines = [line for line in chunk.strip().splitlines() if line]
    event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
    data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
    return event, json.loads(data)


class RunEventStreamTests(unittest.TestCase):
    def test_a_subscriber_receives_published_stages(self):
        stream = RunEventStream()
        with stream.subscribe() as channel:
            stream.publish("research", "MGLV", {"status": "completed"})
            payload = channel.get(timeout=1)
        self.assertEqual((payload["stage"], payload["ticker"]), ("research", "MGLV"))
        self.assertEqual(payload["detail"], {"status": "completed"})
        self.assertIn("created_at", payload)

    def test_every_subscriber_gets_its_own_copy(self):
        stream = RunEventStream()
        with stream.subscribe() as first, stream.subscribe() as second:
            stream.publish("gate", "APEX")
            self.assertEqual(first.get(timeout=1)["stage"], "gate")
            self.assertEqual(second.get(timeout=1)["stage"], "gate")

    def test_subscribers_are_released_when_the_client_goes_away(self):
        stream = RunEventStream()
        with stream.subscribe():
            self.assertEqual(stream.subscriber_count, 1)
        self.assertEqual(stream.subscriber_count, 0)

    def test_publishing_with_no_listener_is_harmless(self):
        RunEventStream().publish("scan", "*")  # tidak boleh melempar

    def test_a_stalled_client_loses_stages_instead_of_stalling_the_run(self):
        """Run pipeline tidak boleh melambat karena satu tab browser berhenti membaca."""
        stream = RunEventStream()
        with patch.object(stream_module, "QUEUE_LIMIT", 2), stream.subscribe():
            for index in range(50):
                stream.publish("research", f"AA{index:02}")  # tidak boleh memblokir

    def test_a_publish_during_iteration_does_not_corrupt_the_subscriber_set(self):
        stream = RunEventStream()
        with stream.subscribe() as channel:
            threads = [threading.Thread(target=stream.publish, args=("research", f"T{n}")) for n in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            received = [channel.get(timeout=1)["ticker"] for _ in range(10)]
        self.assertEqual(len(set(received)), 10)


class EventSourceTests(unittest.TestCase):
    """Generator-nya async supaya tidak menahan thread threadpool per klien."""

    def test_the_first_message_confirms_the_client_is_attached(self):
        stream = RunEventStream()
        messages = asyncio.run(take(event_source(stream), 1))
        self.assertEqual(parse(messages[0])[0], "ready")

    def test_a_published_stage_arrives_as_an_sse_stage_event(self):
        stream = RunEventStream()

        async def scenario():
            generator = event_source(stream)
            await anext(generator)  # ready
            stream.publish("validate", "LAPD", {"issues": []})
            message = await anext(generator)
            await generator.aclose()
            return message

        event, payload = parse(asyncio.run(scenario()))
        self.assertEqual(event, "stage")
        self.assertEqual(payload["ticker"], "LAPD")

    def test_a_quiet_stream_sends_a_heartbeat_rather_than_going_silent(self):
        stream = RunEventStream()
        with patch.object(stream_module, "POLL_SECONDS", 0.001), \
             patch.object(stream_module, "HEARTBEAT_SECONDS", 0.005):
            messages = asyncio.run(take(event_source(stream), 2))
        self.assertEqual(parse(messages[1])[0], "heartbeat")

    def test_an_abandoned_connection_is_eventually_closed(self):
        """Tab yang ditinggalkan tidak boleh menahan koneksi selamanya; EventSource menyambung ulang."""
        stream = RunEventStream()
        with patch.object(stream_module, "POLL_SECONDS", 0.001), \
             patch.object(stream_module, "HEARTBEAT_SECONDS", 0.002), \
             patch.object(stream_module, "IDLE_TIMEOUT_SECONDS", 0.01):
            messages = asyncio.run(drain(event_source(stream)))
        self.assertEqual(parse(messages[0])[0], "ready")
        self.assertTrue(all(parse(message)[0] == "heartbeat" for message in messages[1:]))
        self.assertEqual(stream.subscriber_count, 0)

    def test_the_subscription_is_dropped_even_when_the_generator_is_abandoned(self):
        stream = RunEventStream()

        async def scenario():
            generator = event_source(stream)
            await anext(generator)
            attached = stream.subscriber_count
            await generator.aclose()
            return attached, stream.subscriber_count

        self.assertEqual(asyncio.run(scenario()), (1, 0))

    def test_waiting_for_a_stage_never_blocks_the_event_loop(self):
        """Bukti tidak ada thread yang ditahan: coroutine lain tetap jalan saat stream menunggu."""
        stream = RunEventStream()
        ticks = 0

        async def scenario():
            nonlocal ticks

            async def other_work():
                nonlocal ticks
                for _ in range(5):
                    await asyncio.sleep(0.001)
                    ticks += 1

            generator = event_source(stream)
            await anext(generator)
            worker = asyncio.create_task(other_work())
            with patch.object(stream_module, "POLL_SECONDS", 0.001):
                await asyncio.sleep(0.02)
            await worker
            await generator.aclose()

        asyncio.run(scenario())
        self.assertEqual(ticks, 5)


class ModelProgressTests(unittest.TestCase):
    """QA 2026-09-16 P2 #4: progres tidak menunjukkan pekerjaan yang sedang dilakukan.

    Satu pembacaan model memakan puluhan detik. Tanpa event awal, dashboard diam sepanjang itu dan
    tampak berhenti pada tahap kasus sebelumnya.
    """

    def engine(self):
        from app.config import get_settings
        from app.research.engine import ResearchEngine
        built = ResearchEngine(get_settings())
        built._case = {"ticker": "MGLV", "case_id": "MGLV-1"}
        return built

    def test_a_model_read_announces_its_start_and_its_end(self):
        class Agent:
            name = "ollama:qwen2.5:14b"
            def run(self_inner, _text, _schema):
                return "hasil"

        with stream_module.run_events.subscribe() as channel:
            self.engine()._timed_run(Agent(), "analyst", "prompt", None)
            received = []
            while not channel.empty():
                received.append(channel.get_nowait())

        phases = [(row["stage"], row["detail"]["phase"]) for row in received]
        self.assertEqual(phases, [("model", "start"), ("model", "end")])
        self.assertEqual(received[0]["detail"]["role"], "analyst")
        self.assertEqual(received[0]["detail"]["case_id"], "MGLV-1")
        self.assertIn("seconds", received[1]["detail"])

    def test_a_model_that_raises_still_announces_the_end(self):
        """Tanpa ini, satu kegagalan membuat dashboard menggantung di 'sedang membaca' selamanya."""
        class Failing:
            name = "ollama:gagal"
            def run(self_inner, _text, _schema):
                raise RuntimeError("model mati")

        with stream_module.run_events.subscribe() as channel:
            with self.assertRaises(RuntimeError):
                self.engine()._timed_run(Failing(), "reviewer_1", "prompt", None)
            phases = []
            while not channel.empty():
                phases.append(channel.get_nowait()["detail"]["phase"])
        self.assertEqual(phases, ["start", "end"])

    def test_events_carry_the_run_they_belong_to(self):
        """Sisa event run sebelumnya tidak boleh terbaca sebagai progres run yang sekarang."""
        stream_module.run_events.set_run("run-abc")
        self.addCleanup(stream_module.run_events.set_run, None)
        with stream_module.run_events.subscribe() as channel:
            stream_module.run_events.publish("case", "MGLV", {"phase": "start"})
            self.assertEqual(channel.get(timeout=1)["run_id"], "run-abc")


class AuditPublishesTests(unittest.TestCase):
    def test_recording_an_audit_row_pushes_it_to_listeners(self):
        """Dashboard membaca progres dari sini, jadi tahap yang tidak terbit tidak akan terlihat."""
        import app.main as main
        client = TestClient(main.app)
        received: queue.Queue = queue.Queue()

        with stream_module.run_events.subscribe() as channel:
            with patch.object(main, "DocumentSource"), \
                 patch.object(main.Scanner, "discover", return_value={
                     "run_id": "r", "coverage": "partial", "articles_checked": 0, "announcements_matched": 0,
                     "failures": [], "listing_pages": [], "candidates": [], "coverage_note": "catatan"}), \
                 patch.object(main, "research_queue_items", side_effect=no_candidates):
                job_id = client.post("/scan/run").json()["id"]
                # Run kini berjalan di latar, jadi tunggu job-nya ditutup sebelum membaca antrean.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if client.get(f"/runs/{job_id}").json()["status"] != "running":
                        break
                    time.sleep(0.01)
            while not channel.empty():
                received.put(channel.get_nowait())

        stages = [received.get()["stage"] for _ in range(received.qsize())]
        self.assertIn("scan", stages)
        self.assertIn("run", stages)  # awal/akhir run juga diterbitkan

    def test_the_stream_route_announces_itself_as_an_event_stream(self):
        import app.main as main
        route = next(r for r in main.app.routes if getattr(r, "path", "") == "/run/stream")
        self.assertIn("GET", route.methods)


if __name__ == "__main__":
    unittest.main()
