"""Reproduksi observasi QA 16 September 2026; model dan sumber seluruhnya palsu.
Jalankan dari backend: .venv/bin/python ../docs/qa_2026_09_16_reproduce.py
Tidak mengakses jaringan, database kerja, atau direktori kasus kerja.
Output menunjukkan perilaku saat review; ini bukan suite pengujian regresi.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from app.config import Settings
from app.pipeline.schema import CandidateEvent, ActionBucket, Verdict, VerdictLabel
from app.research.models import ResearchOutcome
from app.scan import research_queue_items, write_json, Scanner

settings=Settings(_env_file=None, llm_backend='off', sectors_api_enabled=False)
def seed(directory, statuses):
    for i,status in enumerate(statuses):
        event=CandidateEvent(ticker=f'AA{i:02}', headline='Rights issue September 2026',body='',source_url=f'https://source.test/{i}',published_at='2026-09-16',bucket=ActionBucket.rights_issue,matched_keywords=['rights issue'])
        write_json(directory/'queue'/f'{i:02}.json',{'id':f'{i:02}','event':event.model_dump(mode='json'),'status':status,'pdf_urls':[]})
class Engine:
    settings=None
    def research(self,event,*args,**kwargs):
        return ResearchOutcome(status='completed' if event.ticker=='AA03' else 'insufficient_evidence',verdict=Verdict(label=VerdictLabel.inconclusive,confidence=0,provider='qa'))
    def close(self): pass
with tempfile.TemporaryDirectory() as folder:
    d=Path(folder)
    seed(d,['pending_pdf_review']*4)
    with patch('app.scan.build_provider',return_value=Engine()):
        first=[e.ticker for e,_,_ in research_queue_items(settings,d,3)]
        second=[e.ticker for e,_,_ in research_queue_items(settings,d,3)]
    print('QUEUE_STARVATION',json.dumps({'first_run':first,'second_run':second,'fourth_status':json.loads((d/'queue'/'03.json').read_text())['status']}))
class CompletedEngine(Engine):
    def research(self,event,*args,**kwargs):
        return ResearchOutcome(status='completed',verdict=Verdict(label=VerdictLabel.inconclusive,confidence=0,provider='qa'))
with tempfile.TemporaryDirectory() as folder:
    d=Path(folder);seed(d,['pending_pdf_review'])
    with patch('app.scan.build_provider',return_value=CompletedEngine()):
        generator=research_queue_items(settings,d,3)
        next(generator)
        # Simulate persist_screened_event failing in the endpoint after it receives the outcome.
        generator.close()
        second=list(research_queue_items(settings,d,3))
    print('PERSIST_FAILURE',json.dumps({'queue_status_after_failed_publish':json.loads((d/'queue'/'00.json').read_text())['status'],'retry_result_count':len(second)}))
with tempfile.TemporaryDirectory() as folder:
    d=Path(folder)
    report={'candidates':[{'id':'article','event':{'ticker':'TEST','matched_keywords':['rights issue'],'published_at':'2026-09-16'},'status':'needs_document'}]}
    Scanner(None,d).attach_announcement_documents(report,[({'ticker':'TEST','urls':['https://idx.test/2024/rights-issue.pdf']},['rights issue'],'idx.test'),({'ticker':'TEST','urls':['https://idx.test/2026/rights-issue.pdf']},['rights issue'],'idx.test')])
    print('CROSS_EVENT_DOCUMENTS',json.dumps(report))
from queue import Empty
from app.pipeline import orchestrator
from app.pipeline.stream import run_events
from app.db.session import build_session_factory
def event(ticker):
    return CandidateEvent(ticker=ticker, headline='Rights issue', body='',
                          source_url='https://source.test/event', published_at='',
                          bucket=ActionBucket.rights_issue, matched_keywords=['rights issue'])
class Client: last_report=None
class ProgressEngine(CompletedEngine):
    def research(self,e,*args,**kwargs):
        pending=[]
        while True:
            try: pending.append(channel.get_nowait()['stage'])
            except Empty: break
        print('PROGRESS_WHILE_MODEL_WORKS',json.dumps({'ticker':e.ticker,'events_since_previous_model_call':pending}))
        return super().research(e)
with tempfile.TemporaryDirectory() as folder:
    factory=build_session_factory(settings.model_copy(update={'signalgate_db_path':str(Path(folder)/'db.sqlite')}))
    with run_events.subscribe() as channel, factory() as session, patch.object(orchestrator,'collect_candidate_events',return_value=[event('AAAA'),event('BBBB')]), patch.object(orchestrator,'snapshot_company',return_value=None):
        orchestrator.run_pipeline(Client(),ProgressEngine(),session)
