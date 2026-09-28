"""Kronologi aksi korporasi dengan fixture sintetis (bukan data HATM asli): T01-T04 dan parsing angka."""
from app.research import chronology
from app.research.chronology import amounts, build_chronology, conflict_claim_ids, entries_from_text, parse_number


def entries(prefix, quote, *, source, published, action="private_placement", claim=None, event_date=None,
            source_text=None, statement=""):
    return entries_from_text(prefix=prefix, action_type=action, action_ref=None, quote=quote, statement=statement,
                             source_id=source, source_text=source_text or quote, published_at=published,
                             event_date_claimed=event_date, claim_id=claim or prefix, origin="local_claim")


def test_numbers_units_and_scales_are_parsed():
    assert parse_number("7,37") == 7.37 and parse_number("1.500") == 1500 and parse_number("1,500.25") == 1500.25
    found = {(item["metric"], item["value"]) for item in amounts(
        "menerbitkan 868 juta saham (7,37%) senilai Rp800 miliar pada harga Rp1.500 per saham, tahun 2026")}
    assert ("shares", 868e6) in found and ("percentage", 7.37) in found
    assert ("value_idr", 800e9) in found and ("price_idr", 1500) in found
    assert all(value != 2026 for _metric, value in found)
    assert ("value_idr", 2.5e12) in {(item["metric"], item["value"]) for item in amounts("dana Rp2,5 triliun")}


def test_t01_revision_with_evidence_keeps_both_stages_and_links_them():
    plan = entries("A", "Perseroan berencana menerbitkan 800 juta saham baru", source="news:a", published="2026-07-01")
    revision = entries("B", "Perseroan merevisi jumlah saham dari semula 800 juta saham menjadi 640 juta saham",
                       source="news:b", published="2026-08-10")
    actions = build_chronology(plan + revision)
    assert len(actions) == 1
    action = actions[0]
    assert [entry["stage"] for entry in action["entries"]] == ["plan", "revision"]
    revised = next(entry for entry in action["entries"] if entry["stage"] == "revision")
    assert revised["value"] == 640e6 and revised["revises_entry_ids"] == ["A:1"]
    assert action["metrics"]["shares"]["terms_value"] == 640e6
    assert action["metrics"]["shares"]["history"] == ["A:1", "B:1"] and action["conflicts"] == []


def test_t02_two_different_actions_are_not_merged_when_the_evidence_names_them():
    first = entries("A", "private placement 640 juta saham", source="news:a", published="2026-03-01",
                    source_text="Surat No. 001/HATX/III/2026: private placement 640 juta saham")
    second = entries("B", "private placement 868 juta saham", source="news:b", published="2026-08-01",
                     source_text="Surat No. 045/HATX/VIII/2026: private placement 868 juta saham")
    everything = first + second
    chronology.assign_refs(everything, ["No. 001/HATX/III/2026", "No. 045/HATX/VIII/2026"],
                           {"news:a": "Surat No. 001/HATX/III/2026: private placement 640 juta saham",
                            "news:b": "Surat No. 045/HATX/VIII/2026: private placement 868 juta saham"})
    actions = build_chronology(everything)
    assert len(actions) == 2 and all(action["conflicts"] == [] for action in actions)
    assert {action["action_ref"] for action in actions} == {"no. 001/hatx/iii/2026", "no. 045/hatx/viii/2026"}


def test_t02_without_identity_same_type_different_numbers_is_uncertain_not_merged_into_one_value():
    actions = build_chronology(entries("A", "private placement 640 juta saham", source="news:a", published="2026-03-01")
                               + entries("B", "private placement 868 juta saham", source="news:b",
                                         published="2026-08-01"))
    assert actions[0]["identity"] == "unproven"
    assert actions[0]["metrics"]["shares"]["terms_value"] is None and actions[0]["conflicts"]


def test_t03_publication_date_is_never_copied_into_the_event_date():
    unwritten = entries("A", "menerbitkan 868 juta saham baru", source="news:a", published="2026-08-20",
                        event_date="2026-08-15")[0]
    assert unwritten.event_date is None and unwritten.event_date_claimed == "2026-08-15"
    assert unwritten.published_at == "2026-08-20"
    written = entries("B", "menerbitkan 868 juta saham baru", source="news:b", published="2026-08-20",
                      event_date="2026-08-15",
                      source_text="RUPSLB pada 15 Agustus 2026 menyetujui penerbitan; menerbitkan 868 juta saham baru")[0]
    assert written.event_date == "2026-08-15"


def test_t04_newest_article_without_revision_evidence_does_not_become_the_value():
    older = entries("A", "private placement sebanyak 800 juta saham", source="news:a", published="2026-06-01",
                    claim="news:model:1")
    newer = entries("B", "private placement sebanyak 868 juta saham", source="news:b", published="2026-09-01",
                    claim="news:model:2")
    actions = build_chronology(older + newer)
    shares = actions[0]["metrics"]["shares"]
    assert shares["terms_value"] is None and "tidak dipilih otomatis" in shares["terms_note"]
    assert conflict_claim_ids(actions) == {"news:model:1", "news:model:2"}
    assert chronology.conflict_messages(actions)[0].startswith("Kronologi ")


def test_realization_is_compared_separately_from_the_plan():
    actions = build_chronology(
        entries("A", "berencana menerbitkan 868 juta saham", source="news:a", published="2026-05-01")
        + entries("B", "telah menyelesaikan penerbitan 800 juta saham", source="news:b", published="2026-09-01"))
    shares = actions[0]["metrics"]["shares"]
    assert shares["terms_value"] == 868e6 and shares["realized_value"] == 800e6 and actions[0]["conflicts"] == []


def test_two_numbers_inside_one_quote_are_not_a_cross_source_conflict():
    actions = build_chronology(entries("A", "kepemilikan naik dari 7% menjadi 10% setelah transaksi",
                                       source="news:a", published="2026-05-01"))
    assert actions[0]["conflicts"] == []


def test_statement_numbers_are_used_only_when_written_in_the_source():
    kept = entries("A", "perseroan menambah modal", statement="TEST menerbitkan 868 juta saham", source="news:a",
                   published="2026-05-01", source_text="perseroan menambah modal dengan 868 juta saham")
    dropped = entries("B", "perseroan menambah modal", statement="TEST menerbitkan 999 juta saham", source="news:b",
                      published="2026-05-01", source_text="perseroan menambah modal")
    assert [entry.value for entry in kept] == [868e6] and dropped == []
