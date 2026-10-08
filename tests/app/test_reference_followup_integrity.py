"""Offline proof and concurrency boundaries for saved-answer follow-ups."""
from copy import deepcopy
import hashlib

import pytest

from app.db import DomainError, dumps
from app.reference_results import ReferenceResultStore
from .test_reference_cases import _followup_result, _result
from .test_reference_model_identity import _result_with_receipts
from .test_reference_results import _saved_run


def _visual_case(client, project):
    db, origin, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    question = 'What approved color applies to Finish key PT9?'
    source = _result(client.app.state.reference_results, origin, question)
    cases = client.app.state.reference_cases
    case = cases.create(project['id'], origin['id'], question, result_id=source,
                        attachments=[document['document_id']])
    run, _ = _followup_result(db, client, project, document, question)
    value = _result_with_receipts(db, run, question,
                                 models=['vision-model', 'text-model'], visual_rounds=[1])
    # Only the first image round contains this attachment. It is not a text
    # input or a final-round image, and does not support a published claim.
    for receipt in value['execution_receipts']:
        receipt.update(evidence_inputs=[], evidence_count=0, source_text_bytes=0,
                       ordered_evidence_manifest_sha256=hashlib.sha256(dumps([]).encode()).hexdigest())
    value.update(status='CANNOT_ANSWER', answer='', claims=[], missing=['Synthetic missing detail.'],
                 visual_regions=[], model_call_count=2)
    saved = client.app.state.reference_results.save(run, question, value, 'mock', 'ignored-label')
    return db, cases, case, run, document, saved


def test_followup_proves_early_visual_only_input_and_complete_model_route(client, project):
    db, cases, case, run, document, saved = _visual_case(client, project)
    before = db.one('SELECT result_hash,result_json FROM reference_results WHERE id=?',
                    (saved['result_id'],))
    updated = cases.link_followup(case['case_id'], case['version'], run['id'],
                                  saved['result_id'], [document['document_id']], '')
    proof = updated['followups'][0]['proof']
    attachment = proof['documents'][0]
    assert attachment['text_input_rounds'] == []
    assert attachment['visual_input_rounds'] == [1]
    assert attachment['citation_count'] == 0
    assert attachment['receipts'][0]['visual_inputs'][0]['image_sha256'] == '0' * 64
    assert proof['models_used'] == ['vision-model', 'text-model']
    assert proof['terminal_model'] == 'text-model'
    assert [step['input_mode'] for step in proof['execution_route']] == ['VISION', 'TEXT']
    assert proof['proof_sha256'] == hashlib.sha256(
        dumps({key: value for key, value in proof.items() if key != 'proof_sha256'}).encode()).hexdigest()
    assert updated['status'] == 'IN_REVIEW'
    assert before == db.one('SELECT result_hash,result_json FROM reference_results WHERE id=?',
                             (saved['result_id'],))


@pytest.mark.parametrize('field', ['image_sha256', 'document_id'])
def test_followup_rejects_tampered_sent_visual_identity(client, project, field):
    db, cases, case, run, document, saved = _visual_case(client, project)
    value = deepcopy(saved['result'])
    value['sent_visual_regions'][0][field] = 'f' * 64 if field == 'image_sha256' else 'DOC-other'
    raw = dumps(value)
    # Keep the outer result checksum consistent so the inner provenance
    # validator, rather than the simple JSON checksum, must detect this.
    db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
               (raw, hashlib.sha256(raw.encode()).hexdigest(), saved['result_id']))
    with pytest.raises(DomainError, match='visual'):
        cases.link_followup(case['case_id'], case['version'], run['id'],
                            saved['result_id'], [document['document_id']], '')
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'] == 0


def test_followup_rechecks_version_after_authentication_without_partial_write(client, project, monkeypatch):
    db, cases, case, run, document, saved = _visual_case(client, project)
    authenticate = ReferenceResultStore.authenticate_saved_result
    original_events = db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    def concurrent_update(store, source_run, row):
        result = authenticate(store, source_run, row)
        db.execute('UPDATE reference_cases SET version=version+1 WHERE id=?', (case['case_id'],))
        return result
    monkeypatch.setattr(ReferenceResultStore, 'authenticate_saved_result', concurrent_update)
    with pytest.raises(DomainError, match='changed'):
        cases.link_followup(case['case_id'], case['version'], run['id'],
                            saved['result_id'], [document['document_id']], '')
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'] == 0
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n'] == original_events
