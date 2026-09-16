import json
import unittest
import tests.test_research_engine as fixtures
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.pipeline.schema import ActionBucket, Verdict, VerdictLabel
from app.pipeline.presentation import screen_outcome
from app.pipeline.watch import next_watch_status
from app.research.facts import locate_quote, party_name_issue, apply_validation, compare_independent_facts, verified_facts
from app.research.models import Evidence, Extraction, Validation, Fact, ResearchOutcome
from app.research.scoring import MarketContext, decide, fact_signals, market_signals, score_signals
from tests.test_research_engine import ScriptedModel, extraction, validation, make_event, SOURCE_URL, ARTICLE


def fact(value='working_capital', **updates):
    return Fact(**({'id':'F01','topic':'use_of_funds','value':value,'claim':'',
                   'quote':'Dana digunakan untuk modal kerja perseroan.', 'evidence_id':'E002'} | updates))


def test_generic_description_with_acronym_is_not_specific_party():
    assert 'terlalu umum' in party_name_issue('pemegang saham utama NDC', 'existing_shareholder',
                                             'pemegang saham utama NDC akan menyerap saham')


def test_issuer_is_not_its_own_external_counterparty():
    assert 'emiten sendiri' in party_name_issue('PT Contoh Tbk', 'affiliate',
                                               'PT Contoh Tbk menyampaikan rencana', ['PT Contoh Tbk'])


def test_fuzzy_match_cannot_discard_inserted_negation():
    assert locate_quote('tidak Dana digunakan untuk ekspansi bisnis inti perseroan.',
                        'Dana digunakan untuk ekspansi bisnis inti perseroan.') is None


def test_changed_number_in_quote_is_not_accepted():
    assert locate_quote('100 miliar dana digunakan untuk ekspansi bisnis inti perseroan.',
                        '900 miliar dana digunakan untuk ekspansi bisnis inti perseroan.') is None


def test_working_capital_plus_existing_holder_is_not_growth():
    signals = fact_signals([fact(), fact('existing_shareholder', topic='counterparty', claim='PT Contoh', id='F02')])
    assert decide(signals)[0] == VerdictLabel.inconclusive


def test_duplicate_validator_ids_fail_closed():
    checks = [{'fact_id':'F01','status':'supported','reason':'ok'}] * 2
    kept, issues = apply_validation([fact()], Validation(checks=checks, agrees_with_label=True, issues=[]), False)
    assert not kept and issues


def test_blind_classification_disagreement_is_detected():
    result = compare_independent_facts([fact('core_expansion')], [fact('new_business')])
    assert result.checks[0].status == 'contradicted'


def test_party_abbreviation_in_parentheses_is_the_same_party():
    analyst = fact('affiliate', topic='counterparty', claim='PT Multi Sarana Nasional',
                   quote='PT Multi Sarana Nasional (MSN)')
    reader = fact('affiliate', topic='counterparty', claim='PT Multi Sarana Nasional (MSN)',
                  quote='saham baru akan diterbitkan kepada PT Multi Sarana Nasional (MSN)')
    assert compare_independent_facts([analyst], [reader]).checks[0].status == 'supported'


def test_sentence_split_into_several_uses_still_supports_each_use():
    sentence = 'belanja modal, khususnya penambahan aset berupa armada kapal, atau pembayaran pinjaman bank'
    readers = [fact('core_expansion', quote='belanja modal'), fact('debt_repayment', quote='pembayaran pinjaman bank')]
    assert compare_independent_facts([fact('core_expansion', quote=sentence)], readers).checks[0].status == 'supported'


def page(evidence_id, text):
    return Evidence(id=evidence_id, kind='pdf', url=f'https://idx.test/doc.pdf#{evidence_id}', title='doc', text=text,
                    links=[], retrieved_at='2026-09-16T00:00:00+00:00', sha256='0' * 64)


def test_verbatim_quote_cited_to_wrong_page_is_rehomed_not_rewritten():
    payload = extraction()
    payload['counterparties'][0]['evidence_id'] = 'E009'
    payload['use_of_funds'][0]['evidence_id'] = 'E009'
    store = SimpleNamespace(items=[page('E002', ARTICLE), page('E009', 'Halaman lain yang tidak memuat kutipan.')])
    facts, issues = verified_facts(Extraction.model_validate(payload), store)
    assert [f.evidence_id for f in facts] == ['E002', 'E002'] and not issues
    payload['use_of_funds'][0]['quote'] = 'Dana digunakan untuk menambah armada kapal tanker'
    facts, issues = verified_facts(Extraction.model_validate(payload), store)
    assert [f.topic for f in facts] == ['counterparty'] and issues


def test_same_party_and_relation_on_another_page_supports_the_fact():
    analyst = fact('affiliate', topic='counterparty', claim='PT Asiatic Sejahtera Finance', evidence_id='E007',
                   quote='PT Asiatic Sejahtera Finance (selanjutnya disebut ASF)')
    reader = fact('affiliate', topic='counterparty', claim='PT Asiatic Sejahtera Finance', evidence_id='E038',
                  quote='PT Asiatic Sejahtera Finance selaku pihak terafiliasi')
    assert compare_independent_facts([analyst], [reader]).checks[0].status == 'supported'


def test_same_use_category_from_another_sentence_is_not_support():
    analyst = fact('working_capital', quote='Rp600 miliar akan diberikan untuk pengembangan fasilitas data center')
    reader = fact('working_capital', quote='Sisa dana dari rights issue akan digunakan untuk kebutuhan modal kerja')
    assert compare_independent_facts([analyst], [reader]).checks[0].status == 'not_supported'


def test_sectors_market_data_alone_can_never_produce_a_label():
    """Free float, cash burn and PBV describe the stock, not the corporate action.

    Together they must still land on inconclusive: without a verified fact from the source document
    there is nothing to screen, and a label built only from market data would be a stock call.
    """
    loud = MarketContext(pb_ratio=256.5, bucket=ActionBucket.rights_issue, free_float=0.0749,
                         free_float_rank=1, free_float_universe=48,
                         quarters=tuple({'date': d, 'operating_cash_flow': -1, 'revenue': r}
                                        for d, r in zip(['2026-06-30', '2026-03-31', '2025-12-31', '2025-09-30'],
                                                        [100, 200, 300, 400])))
    signals = market_signals(loud)
    assert len(signals) == 3 and all(signal.side == 'red' for signal in signals)
    assert decide(signals) == (VerdictLabel.inconclusive, 0.0)
    assert decide(score_signals(loud, [])) == (VerdictLabel.inconclusive, 0.0)


def test_prices_do_not_resolve_fundamental_theses():
    assert next_watch_status('active', VerdictLabel.growth_catalyst, 1, 20) == 'active'
    assert next_watch_status('active', VerdictLabel.structural_red_flag, 1, 0) == 'active'
    assert next_watch_status('active', VerdictLabel.growth_catalyst, 31, 20) == 'stale'


def test_both_routes_use_same_publication_gate():
    outcome = ResearchOutcome(status='needs_review', verdict=Verdict(label='growth_catalyst', confidence=.8,
                                provider='test', rationale_bullets=['Beli sekarang']))
    screened = screen_outcome(make_event(), None, outcome)
    assert screened.gate.status == 'needs_review'
    assert 'Beli' not in ' '.join(screened.verdict.rationale_bullets)


class EngineArchitectureTests(unittest.TestCase):
    setUp = fixtures.ResearchEngineTests.setUp
    tearDown = fixtures.ResearchEngineTests.tearDown
    engine = fixtures.ResearchEngineTests.engine
    def test_validator_never_receives_analyst_answers(self):
        analyst = ScriptedModel([extraction()])
        reviewer = ScriptedModel([extraction()])
        self.engine(analyst, reviewer).research(make_event(), None)
        data = json.loads(reviewer.prompts[0].split('DATA JSON:\n')[1])
        self.assertNotIn('facts', data)
        self.assertNotIn('label', data)
        self.assertNotIn('previous_issues', data)

    def test_changed_source_invalidates_cache(self):
        self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        self.scraper.pages[SOURCE_URL] = ('Revisi artikel', ARTICLE + '\nRencana berubah.', [])
        model = ScriptedModel([extraction(), validation()])
        outcome = self.engine(model).research(make_event(), None)
        self.assertFalse(outcome.cached)
        self.assertEqual(len(model.prompts), 2)

    def test_changed_event_body_invalidates_cache(self):
        self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        event = make_event().model_copy(update={'body':'Pengumuman revisi'})
        outcome = self.engine(ScriptedModel([extraction(), validation()])).research(event, None)
        self.assertFalse(outcome.cached)

    def test_expired_cache_is_not_used(self):
        self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        path = next((self.cases_dir / '_cache').glob('*.json'))
        data = json.loads(path.read_text())
        data['generated_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        path.write_text(json.dumps(data))
        outcome = self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        self.assertFalse(outcome.cached)

    def test_scraper_failure_cannot_return_previous_success(self):
        self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        self.scraper.pages.clear()
        empty = {**extraction(), 'counterparties':[], 'use_of_funds':[]}
        outcome = self.engine(ScriptedModel([empty])).research(make_event(), None)
        self.assertFalse(outcome.cached)
        self.assertEqual(outcome.status, 'insufficient_evidence')

    def test_public_workflow_requires_data_not_just_api_key(self):
        model = ScriptedModel([])
        outcome = self.engine(model).research(make_event(), None, report={}, require_sectors=True)
        self.assertEqual(outcome.status, 'missing_sectors_data')
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)
        self.assertFalse(model.prompts)


def test_unreviewed_model_outputs_never_count_as_ground_truth():
    from app.evaluate import summarize
    row = {'label':'growth_catalyst', 'status':'completed', 'expected_label':None}
    result = summarize([row])
    assert result['human_reviewed_cases'] == 0
    assert result['decided_precision'] is None


def test_evaluation_reports_abstentions_and_false_growth_separately():
    from app.evaluate import summarize
    common = {'status':'completed', 'reviewer':'human', 'review_notes':'checked sources'}
    result = summarize([
        common | {'label':'growth_catalyst', 'expected_label':'structural_red_flag'},
        common | {'label':'inconclusive', 'expected_label':'growth_catalyst'},
    ])
    assert result['false_growth_count'] == 1
    assert result['reviewed_decision_coverage'] == .5
    assert result['decided_precision'] == 0


def test_replay_uses_frozen_sources_without_live_sectors(monkeypatch, tmp_path):
    from argparse import Namespace
    from app import evaluate
    from app.config import Settings
    from app.research.engine import ResearchEngine
    from app.research.evidence import EvidenceStore
    directory = tmp_path / 'source'
    store = EvidenceStore(directory, scraper=fixtures.FakeScraper({}))
    store.add('scrapling', SOURCE_URL, 'HATM', ARTICLE)
    report = {'company_name':'PT Habco Trans Maritima Tbk', 'overview':{'industry':'Shipping'}}
    evaluate.write_json(directory / 'evaluation_input.json', {'event':make_event().model_dump(mode='json'), 'report':report})
    manifest = tmp_path / 'manifest.json'
    evaluate.write_json(manifest, {'cases':[{'directory':str(directory), 'expected_label':None}]})
    values = extraction()
    for entry in values['counterparties'] + values['use_of_funds']:
        entry['evidence_id'] = 'E003'
    model = ScriptedModel([values, values])
    monkeypatch.setattr(evaluate, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(evaluate, 'build_provider', lambda settings: ResearchEngine(settings, model=model))
    def fail_network(*args, **kwargs):
        raise AssertionError('Replay must not call Sectors')
    monkeypatch.setattr(evaluate, 'SectorsClient', fail_network)
    evaluate.replay(Namespace(manifest=str(manifest), output=str(tmp_path / 'replayed')))
    result = json.loads((tmp_path / 'replayed' / 'report.json').read_text())
    assert result['summary']['completed_cases'] == 1
    assert result['summary']['human_reviewed_cases'] == 0


def test_ollama_non_object_response_fails_cleanly():
    import httpx
    from app.research.agents import AgentError
    from tests.test_ollama_agent import agent_with
    import pytest
    with pytest.raises(AgentError):
        agent_with(lambda request: httpx.Response(200, json=[])).run('prompt', Extraction)
