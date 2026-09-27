"""Project-file Q&A stays run-scoped, evidence-grounded, and budgeted."""
from decimal import Decimal
from email.message import EmailMessage
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

import app.questions as questions_module
from app.db import BudgetError, Database, dumps, paid_task_key
from app.gateway import Gateway, InvalidModelOutput
from app.questions import (
    ProjectQuestions, _clause_identifier_values, _drawing_identifier_values,
    _email_address_role_values, _email_address_values,
    _email_action_claim_values, _email_action_source_values,
    _email_date_header_claim_values, _email_date_header_values,
    _email_header_participant_role_values, _email_participant_claim_role_values,
    _numeric_unit_values,
    _cross_workflow_relation_claim_values, _cross_workflow_relation_source_values,
    _requested_source_families, _revision_label_values, _source_family,
    _spec_section_values, _workflow_identities,
    _workflow_party_claim_values, _workflow_party_source_values,
    _workflow_action_claim_values, _workflow_action_source_values,
    _workflow_field_claim_values, _workflow_field_source_values,
    _workflow_impact_claim_values, _workflow_impact_source_values,
    _workflow_metadata_claim_values, _workflow_metadata_source_values,
    _workflow_subject_claim_values, _workflow_subject_source_values,
    _workflow_index_status_conflicts,
    expanded_query_terms,
    numeric_rfi_search_terms, requested_workflow_counts, requested_workflow_list,
    requested_email_action, requested_email_attachment_relations, requested_email_header,
    requested_email_thread, requested_email_workflow_relations,
    requested_single_email_header,
    requested_rfi_content, requested_rfi_spec_section, requested_submittal_field,
    requested_workflow_clause_references,
    requested_workflow_date_item, requested_workflow_document_relations,
    requested_workflow_drawing_references,
    requested_workflow_email_relations,
    requested_cross_workflow_relations,
    requested_workflow_action, requested_workflow_field, requested_workflow_impact,
    requested_workflow_metadata, requested_workflow_party,
    requested_workflow_status_inventory, requested_workflow_status_item,
    requested_workflow_subject_item,
    requires_source_diversity,
    requires_email_action_index, requires_email_attachment_relation_index,
    requires_email_header_index,
    requires_email_thread_index,
    requires_email_workflow_relation_index,
    requires_rfi_content_index,
    requires_rfi_spec_section_index,
    requires_submittal_field_index,
    requires_single_email_header_index,
    requires_workflow_clause_reference_index,
    requires_workflow_date_index,
    requires_workflow_document_relation_index,
    requires_workflow_drawing_reference_index,
    requires_workflow_email_relation_index,
    requires_cross_workflow_relation_index,
    requires_workflow_inventory, requires_workflow_status_index,
    requires_workflow_action_index, requires_workflow_field_index,
    requires_workflow_impact_index,
    requires_workflow_metadata_index,
    requires_workflow_party_index,
    requires_workflow_subject_index, retrieve_evidence,
    search_query_terms, validate_answer_model,
)
from app.settings import ROOT, Settings
from app.workflows import build_workflow_index
from .conftest import upload


def _evidence(client, project, rows):
    document = upload(client, project['id'], 'project-spec.txt', b'project source')
    run = client.app.state.runner.create(project['id'])
    db = client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run['id'],))
    base = json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    values = []
    for index, text in enumerate(rows, 1):
        eid = f'EV-QA-{index}'
        payload = {**base, 'evidence_id': eid, 'tenant_id': 'local',
                   'project_id': project['id'], 'input_snapshot_id': run['snapshot_id'],
                   'document_id': document['document_id'], 'raw_text': text,
                   'locator': {**base['locator'], 'section': f'Section {index}'}}
        values.append((run['id'] + ':' + eid, run['id'], project['id'], document['document_id'],
                       json.dumps(payload), 'EXTRACTED', None, ''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)', values)
    return client.app.state.runner.get(run['id'])


def _source_evidence(client, project, sources):
    documents=[]
    for name,rows in sources:
        document=upload(client,project['id'],name,(name+' source').encode())
        documents.append((name,document,rows))
    run=client.app.state.runner.create(project['id'])
    db=client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    values=[];index=0
    for name,document,rows in documents:
        for text in rows:
            index+=1;eid=f'EV-SOURCE-{index}'
            payload={**base,'evidence_id':eid,'tenant_id':'local','project_id':project['id'],
                     'input_snapshot_id':run['snapshot_id'],'document_id':document['document_id'],
                     'raw_text':text,'locator':{**base['locator'],'section':name}}
            values.append((run['id']+':'+eid,run['id'],project['id'],document['document_id'],
                           json.dumps(payload),'EXTRACTED',None,''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',values)
    return client.app.state.runner.get(run['id'])


def _live_settings(tmp_path):
    return Settings(tmp_path, provider='deepseek', live_enabled=True, prices_confirmed=True,
                    api_key='test-not-real', input_rate=Decimal('1'),
                    output_rate=Decimal('2'), start_worker=False)


def test_retrieval_ranks_specific_project_evidence(client, project):
    run = _evidence(client, project, [
        'Landscaping irrigation requirements are shown elsewhere.',
        'Domestic water service pipe shall be 2 inch Type L copper.',
        'Concrete compressive strength shall be 4,000 psi.',
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-2'
    assert all(item['project_id'] == project['id'] for item in found)


def test_retrieval_does_not_lose_late_exact_match_behind_common_terms(client, project):
    rows = [f'General water coordination note {index}.' for index in range(160)]
    rows.append('RFI 42 response: Domestic water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'What does RFI 42 require for the water service pipe material and size?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_matches_a_zero_padded_numeric_rfi_reference_under_candidate_pressure(
        client, project):
    rows = [
        f'Specification note references an RFI response for water service pipe material size '
        f'requirements {index}.' for index in range(160)
    ]
    rows.append(
        'Specification note references RFI 0042 response: '
        'Water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    db=client.app.state.db
    found = retrieve_evidence(
        db,
        run,
        'What does RFI 42 require for the water service pipe material and size?',
    )
    db.evidence_search_available=False
    fallback=retrieve_evidence(
        db,run,'What does RFI 42 require for the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-161'
    assert fallback and fallback[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_matches_a_zero_padded_rfi_in_email_under_candidate_pressure(
        client, project):
    rows = [
        'From: contractor@example.test\nSubject: RFI response\n'
        f'General response coordination note {index}.' for index in range(160)
    ]
    rows.append(
        'From: architect@example.test\n'
        'Subject: Request for Information No. 00077 response\n'
        'Request for Information No. 00077 response: Use the reviewed Type L copper detail.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Who answered Request for Information No. 77 in the email?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


@pytest.mark.parametrize(('question','expected'),[
    ('What does RFI ARC-42 require?','EV-QA-2'),
    ('What is the status of Submittal 23-01?','EV-QA-4'),
])
def test_zero_padding_does_not_merge_prefixed_rfi_or_submittal_identifiers(
        client, project, question, expected):
    run = _evidence(client, project, [
        'RFI ARC-0042 response: Use copper pipe.',
        'RFI ARC-42 response: Use stainless steel pipe.',
        'Submittal 23-001 status: Pending.',
        'Submittal 23-01 status: Approved as noted.',
    ])

    found=retrieve_evidence(client.app.state.db,run,question)

    assert found and found[0]['evidence_id']==expected


def test_numeric_rfi_search_aliases_are_bounded_and_require_a_pure_numeric_identifier():
    primary=search_query_terms(
        'What did RFI 42 require for the water service pipe material size and inspection?')
    aliases=numeric_rfi_search_terms(
        'What did RFI 42 require for the water service pipe material size and inspection?')

    assert len(primary)<=24 and len(aliases)<=24
    assert {'rfi 0042','request for information number 0042'}.issubset(aliases)
    assert numeric_rfi_search_terms('What is shown on page 42?')==[]
    assert numeric_rfi_search_terms('What does RFI ARC-42 require?')==[]
    assert numeric_rfi_search_terms('What does RFI 42-1 require?')==[]
    assert numeric_rfi_search_terms('What is the status of Submittal 42?')==[]


def test_retrieval_maps_question_terms_to_bounded_workflow_synonyms(client, project):
    rows = ['Submittal 23-01 cover sheet.' for _ in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted for AHU-1.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Was Submittal 23-01 accepted?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_does_not_count_a_synonym_inside_an_unrelated_word(client, project):
    run = _evidence(client, project, [
        'Oversized equipment storage requirements.',
        'Equipment dimensions: 24 x 36 inches.',
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the equipment size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-2'


def test_retrieval_expands_rfi_response_and_email_sender_terms(client, project):
    rows = ['RFI 42 question remains open.' for _ in range(160)]
    rows.append('RFI 42 response: Use 2 inch Type L copper.')
    rows.extend('Email thread for RFI 42.' for _ in range(160))
    rows.append(
        'From: architect@example.com\nSubject: RFI 42 response\nUse 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    rfi = retrieve_evidence(client.app.state.db, run, 'What answer was issued for RFI 42?')
    email = retrieve_evidence(client.app.state.db, run, 'Who sent the reply for RFI 42?')

    assert rfi and rfi[0]['evidence_id'] == 'EV-QA-161'
    assert email and email[0]['evidence_id'] == 'EV-QA-322'


def test_retrieval_treats_workflow_phrases_as_single_bounded_concepts(client, project):
    rows = ['Drawing 23-01 cover page.' for _ in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted.')
    rows.extend('Page 42 general note.' for _ in range(160))
    rows.append('RFI 42 response: Use Type L copper.')
    run = _evidence(client, project, rows)

    submittal = retrieve_evidence(client.app.state.db, run, 'What is Shop Drawing 23-01?')
    rfi = retrieve_evidence(client.app.state.db, run, 'Show Request for Information 42.')

    assert submittal and submittal[0]['evidence_id'] == 'EV-QA-161'
    assert rfi and rfi[0]['evidence_id'] == 'EV-QA-322'


def test_query_expansion_is_bounded_and_never_adds_opposite_status():
    expanded = expanded_query_terms(
        'Was Submittal 23-01 accepted and was RFI 42 answered in the email '
        'specification revision?')

    assert len(expanded) <= 24
    assert {'approved', 'response', 'message'}.issubset(expanded)
    assert 'rejected' not in expanded


def test_comparison_question_keeps_spec_rfi_submittal_and_email_sources(client, project):
    repeated = [
        'Specification comparison note: pipe requirements reference RFI 42, '
        'Submittal 23-01, and the email response.'
    ] * 10
    run = _source_evidence(client, project, [
        ('project-spec.txt', repeated),
        ('RFI-42-response.txt', ['RFI 42 response: Use 2 inch Type L copper pipe.']),
        ('Submittal-23-01.txt', ['Submittal 23-01: Type L copper pipe approved as noted.']),
        ('architect-email.eml', [
            'From: architect@example.com\nSubject: RFI 42 response\nUse Type L copper pipe.']),
    ])

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Compare the pipe requirements in the specification, RFI 42, '
        'Submittal 23-01, and the email response.',
    )

    assert {item['file_name'] for item in found} == {
        'project-spec.txt', 'RFI-42-response.txt', 'Submittal-23-01.txt',
        'architect-email.eml',
    }


@pytest.mark.parametrize('email_text', [
    'To: contractor@example.test\nSubject: Email response: Use Type L copper pipe.',
    'Cc: contractor@example.test\nSubject: Email response: Use Type L copper pipe.',
    'Subject: Email response: Use Type L copper pipe.',
])
def test_generic_pdf_email_source_survives_candidate_pressure(
        client, project, monkeypatch, email_text):
    repeated = ['Specification email response pipe requirements.'] * 16
    run = _source_evidence(client, project, [
        ('project-spec.txt', repeated),
        ('correspondence.pdf', [email_text]),
    ])
    question = 'Compare the pipe requirements in the specification and email response.'

    db = client.app.state.db
    monkeypatch.setattr(questions_module, '_MAX_CANDIDATES', 0)
    found = retrieve_evidence(db, run, question)
    db.evidence_search_available = False
    fallback = retrieve_evidence(db, run, question)

    for result in (found, fallback):
        assert {item['file_name'] for item in result} >= {
            'project-spec.txt', 'correspondence.pdf'}
        assert {_source_family(item) for item in result} >= {'SPECIFICATION', 'EMAIL'}


@pytest.mark.parametrize(('family', 'workflow_text', 'question'), [
    ('RFI',
     'Project correspondence.\nRFI ARC-42 response: Use Type L copper pipe.',
     'Compare the pipe requirements in the specification and RFI ARC-42 response.'),
    ('RFI',
     'Project correspondence.\nRequest for Information ARC-43 response: Use copper pipe.',
     'Compare the specification and Request for Information ARC-43 response.'),
    ('SUBMITTAL',
     'Project correspondence.\nSubmittal 23-01: Type L copper pipe approved as noted.',
     'Compare the pipe requirements in the specification and Submittal 23-01.'),
    ('SUBMITTAL',
     'Project correspondence.\nSubmission 23-02: Copper pipe is pending review.',
     'Compare the pipe requirements in the specification and Submittal 23-02.'),
])
def test_generic_pdf_workflow_heading_after_first_line_survives_candidate_pressure(
        client, project, monkeypatch, family, workflow_text, question):
    repeated = [f'Specification pipe requirements reference {family}.'] * 16
    run = _source_evidence(client, project, [
        ('project-spec.txt', repeated),
        ('coordination.pdf', [workflow_text]),
    ])

    db = client.app.state.db
    monkeypatch.setattr(questions_module, '_MAX_CANDIDATES', 0)
    found = retrieve_evidence(db, run, question)
    db.evidence_search_available = False
    fallback = retrieve_evidence(db, run, question)

    for result in (found, fallback):
        assert {item['file_name'] for item in result} >= {
            'project-spec.txt', 'coordination.pdf'}
        assert {_source_family(item) for item in result} >= {'SPECIFICATION', family}


def test_source_diversity_requires_comparison_or_multiple_named_sources():
    assert requires_source_diversity('Compare the pipe requirements.') is True
    assert requires_source_diversity('What do the specification and RFI 42 require?') is True
    assert requires_source_diversity(
        'What do Drawing A1.01 and the specification require?') is True
    assert requires_source_diversity('Which sheet applies to RFI 42?') is False
    assert requires_source_diversity('What is the pipe size?') is False


def test_drawing_and_specification_question_keeps_both_sources_under_candidate_pressure(
        client, project):
    run=_source_evidence(client,project,[
        ('project-spec.txt',[
            'Specification rated wall requirements reference Drawing A1.01.' for _ in range(160)]),
        ('architectural-drawings.pdf',[
            'Drawing A1.01 rated wall type W1 requires a fire-rated assembly.']),
    ])
    question='What do Drawing A1.01 and the specification require for the rated wall?'

    db=client.app.state.db
    found=retrieve_evidence(db,run,question)
    db.evidence_search_available=False
    fallback=retrieve_evidence(db,run,question)

    for result in (found,fallback):
        assert {item['file_name'] for item in result}>={
            'project-spec.txt','architectural-drawings.pdf'}
        assert {_source_family(item) for item in result}>={'SPECIFICATION','DRAWING'}


def test_drawing_comparison_requires_a_drawing_finding(client, project):
    run=_source_evidence(client,project,[
        ('project-spec.txt',['Specification requires a fire-rated wall assembly.']),
        ('architectural-drawings.pdf',[
            'Drawing A1.01 labels wall type W1 as fire rated.']),
    ])
    question='What do Drawing A1.01 and the specification require for the rated wall?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':'Both sources require a fire-rated wall.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires a fire-rated wall assembly.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification requires a fire-rated wall assembly.'}]},
            {'source_type':'DRAWING','file_name':'architectural-drawings.pdf',
             'statement':'Drawing A1.01 labels wall type W1 as fire rated.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'Drawing A1.01 labels wall type W1 as fire rated.'}]},
        ],
    }

    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='omitted a requested source family'):
        validate_answer_model(
            {**grounded,'source_findings':grounded['source_findings'][:1]},evidence,question)


def test_shop_drawing_stays_submittal_unless_another_drawing_is_named():
    assert set(_requested_source_families(
        'What do the shop drawing and specification require?'))=={
            'SUBMITTAL','SPECIFICATION'}
    assert set(_requested_source_families(
        'Compare the shop drawing, architectural drawings, and specification.'))=={
            'SUBMITTAL','DRAWING','SPECIFICATION'}


@pytest.mark.parametrize(('evidence','expected'),[
    ({'file_name':'A101.pdf','raw_text':'Rated wall type W1.',
      'locator':{'sheet':'A1.01','section':None}},'DRAWING'),
    ({'file_name':'coordination.dwg','raw_text':'Layer schedule.',
      'locator':{'sheet':None,'section':None}},'DRAWING'),
    ({'file_name':'project-spec.txt','raw_text':'Sheet A1.01 is referenced.',
      'locator':{'sheet':None,'section':'Section 07 84 00'}},'SPECIFICATION'),
    ({'file_name':'shop-drawing-23-01.pdf','raw_text':'Product dimensions.',
      'locator':{'sheet':'SD1.01','section':None}},'SUBMITTAL'),
])
def test_drawing_source_family_uses_bounded_signals_after_stronger_types(evidence,expected):
    assert _source_family(evidence)==expected


def test_non_comparison_question_deduplicates_exact_same_source_evidence(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Pipe size shall be 2 inch Type L copper.'] * 8),
        ('RFI-42-response.txt', ['RFI 42 mentions copper piping.']),
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the pipe size?')

    assert len(found) == 1
    assert {item['file_name'] for item in found} == {'project-spec.txt'}


def test_retrieval_keeps_identical_text_from_distinct_documents(client, project):
    text = 'Pipe size shall be 2 inch Type L copper.'
    run = _source_evidence(client, project, [
        ('spec-a.txt', [text]),
        ('spec-b.txt', [text]),
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the pipe size?')

    assert len(found) == 2
    assert {item['file_name'] for item in found} == {'spec-a.txt', 'spec-b.txt'}


def test_retrieval_keeps_identical_text_from_distinct_source_scopes(client, project):
    text = 'Pipe size shall be 2 inch Type L copper.'
    run = _evidence(client, project, [text, text])

    found = retrieve_evidence(client.app.state.db, run, 'What is the pipe size?')

    assert len(found) == 2
    assert {item['locator']['section'] for item in found} == {'Section 1', 'Section 2'}


def test_comparison_diversifies_workflow_sections_inside_one_document(client, project):
    rows = [
        'Specification comparison note: pipe requirements reference RFI 42 response.'
    ] * 200
    rows.append('RFI 42 response: Use 2 inch Type L copper pipe.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db, run, 'Compare the specification requirements and RFI 42 response.')

    assert 'EV-QA-201' in {item['evidence_id'] for item in found}


def test_retrieval_fallback_does_not_apply_row_order_cutoff(client, project):
    rows = [f'General equipment coordination note {index}.' for index in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted for AHU-1.')
    run = _evidence(client, project, rows)
    client.app.state.db.evidence_search_available = False

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'What is the status of Submittal 23-01 for AHU-1?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_ranks_workflow_identifiers_and_email_metadata(client, project):
    run = _evidence(client, project, [
        'General equipment coordination requirements.',
        'Submittal 23-01 status: Approved as noted for AHU-1.',
        'Email subject: RFI 42 response. From: architect@example.com. '
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])

    submittal = retrieve_evidence(
        client.app.state.db, run, 'What is the status of Submittal 23-01 for AHU-1?')
    email = retrieve_evidence(
        client.app.state.db, run, 'What did the architect email say in the RFI 42 response?')

    assert submittal and submittal[0]['evidence_id'] == 'EV-QA-2'
    assert email and email[0]['evidence_id'] == 'EV-QA-3'


def test_retrieval_indexes_updated_sheet_locator(client, project):
    run = _evidence(client, project, ['Mechanical equipment schedule.'])
    db = client.app.state.db
    row = db.one('SELECT id,payload FROM evidence WHERE run_id=?', (run['id'],))
    payload = json.loads(row['payload'])
    payload['locator']['sheet'] = 'M1.1'
    db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))

    found = retrieve_evidence(db, run, 'What is shown on sheet M1.1?')
    db.evidence_search_available = False
    fallback = retrieve_evidence(db, run, 'What is shown on sheet M1.1?')

    assert found and found[0]['evidence_id'] == 'EV-QA-1'
    assert fallback and fallback[0]['evidence_id'] == 'EV-QA-1'


def test_retrieval_keeps_a_late_exact_paragraph_locator_under_candidate_pressure(
        client, project):
    rows=['Paragraph requirements 2 3 1 general note.' for _ in range(160)]
    rows.append('Required hydrostatic test duration is 2 hours.')
    run=_evidence(client,project,rows)
    db=client.app.state.db
    row=db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid DESC LIMIT 1',
               (run['id'],))
    payload=json.loads(row['payload']);payload['locator']['paragraph']='2.3.1'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))

    found=retrieve_evidence(db,run,'What does Paragraph 2.3.1 require?')
    db.evidence_search_available=False
    fallback=retrieve_evidence(db,run,'What does Paragraph 2.3.1 require?')

    assert found and found[0]['evidence_id']=='EV-QA-161'
    assert fallback and fallback[0]['evidence_id']=='EV-QA-161'


def test_excluded_vision_output_cannot_crowd_out_source_evidence(client, project):
    rows = ['RFI 42 water service pipe material size.' for _ in range(160)]
    rows.append('RFI 42 source response: Water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)
    db = client.app.state.db
    for row in db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid LIMIT 160',
                      (run['id'],)):
        payload = json.loads(row['payload'])
        payload['content_basis'] = 'MODEL_VISION_OUTPUT'
        db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))

    found = retrieve_evidence(
        db, run, 'What does RFI 42 require for the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_evidence_search_migration_backfills_an_existing_database(tmp_path):
    path = tmp_path / 'before-evidence-search.sqlite3'
    payload = {
        'evidence_id': 'EV-OLD-1',
        'project_id': 'P-old',
        'document_id': 'D-old',
        'raw_text': 'RFI 77 response requires a 3 inch copper service.',
        'locator': {'section': 'RFI 77'},
    }
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / 'migrations/001_initial.sql').read_text(encoding='utf-8'))
        connection.execute("INSERT INTO projects VALUES('P-old','Old project','now')")
        connection.execute(
            "INSERT INTO documents VALUES('D-old','P-old','old-rfi.txt',1,'sha','object','now')")
        connection.execute('''INSERT INTO runs VALUES(
            'R-old','P-old','mock','SN-old','["D-old"]','COMPLETED','DONE','','now',0,1,
            '{}','{}',0)''')
        connection.execute(
            "INSERT INTO evidence VALUES('E-old','R-old','P-old','D-old',?,'EXTRACTED',NULL,'')",
            (json.dumps(payload),),
        )

    db = Database(path)
    found = retrieve_evidence(db, {'id': 'R-old'}, 'What is required by RFI 77?')

    assert db.evidence_search_available is True
    assert db.one('SELECT COUNT(*) AS count FROM evidence_search')['count'] == 1
    assert db.one('SELECT version FROM schema_migrations WHERE version=7')['version'] == 7
    assert found and found[0]['evidence_id'] == 'EV-OLD-1'


def test_missing_fts5_uses_supported_fallback():
    class MissingFtsConnection:
        class EmptyResult:
            @staticmethod
            def fetchone():
                return None

        @staticmethod
        def execute(query, args=()):
            return MissingFtsConnection.EmptyResult()

        @staticmethod
        def executescript(script):
            raise sqlite3.OperationalError('no such module: fts5')

    assert Database._install_evidence_search(MissingFtsConnection()) is False


def test_mock_question_returns_retrieval_context_without_fabricated_answer(client, project):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])

    response = client.post(f'/api/projects/{project["id"]}/questions', json={
        'run_id': run['id'], 'question': 'What is the water service pipe material and size?'
    })

    assert response.status_code == 200
    value = response.json()
    assert value['status'] == 'MODEL_DISABLED'
    assert value['answer_basis'] == 'RETRIEVAL_ONLY' and value['cached'] is False
    assert 'Mock mode' in value['answer']
    assert value['citations'][0]['quote'] == 'Domestic water service pipe shall be 2 inch Type L copper.'
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


def test_live_answer_is_exactly_cited_and_budgeted(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    db = client.app.state.db
    response_data = {'status': 'ANSWERED', 'answer': 'Use 2 inch Type L copper.',
                     'source_findings': [], 'citations': [{
        'evidence_id': 'EV-QA-1',
        'quote': 'Domestic water service pipe shall be 2 inch Type L copper.'
    }]}
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'question-test',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(response_data)}}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 30},
        })

    gateway = Gateway(_live_settings(tmp_path), db,
                      httpx.Client(transport=httpx.MockTransport(handler)))
    service = ProjectQuestions(db, gateway)
    result = service.ask(run, 'What is the water service pipe material and size?')
    recovered = service.ask(run, 'What is the water service pipe material and size?')
    equivalent = service.ask(run, 'What  is the water service pipe material and size?')

    assert result['status'] == 'ANSWERED' and result['answer'] == 'Use 2 inch Type L copper.'
    assert result['answer_basis'] == 'MODEL_PROJECT_EVIDENCE' and result['cached'] is False
    assert result['citations'][0]['evidence_id'] == 'EV-QA-1'
    assert result['citations'][0]['start'] == 0
    assert recovered['answer'] == result['answer'] and recovered['cached'] is True
    assert recovered['answer_basis'] == 'MODEL_PROJECT_EVIDENCE'
    assert equivalent['answer'] == result['answer'] and equivalent['cached'] is True
    assert len(requests) == 1
    assert 'Domestic water service pipe' not in requests[0]['messages'][0]['content']
    assert 'Domestic water service pipe' in requests[0]['messages'][1]['content']
    call = db.one('SELECT task_key,state FROM model_calls WHERE run_id=?', (run['id'],))
    assert call['task_key'].startswith('answer:') and call['state'] == 'SETTLED'


def test_question_recovers_a_settled_pre_normalization_task_without_http(client, project, tmp_path):
    run = _evidence(client, project, [
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])
    question='What  is the water service pipe material and size?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    settings=_live_settings(tmp_path)
    requests=[]
    gateway=Gateway(settings,client.app.state.db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    evidence_fingerprint=[
        (item['evidence_id'],item.get('prompt_text',item['raw_text'])) for item in evidence]
    legacy=[run['project_id'],run['snapshot_id'],settings.api_base_url,settings.cheap_model,
            gateway.answer_prompt_hash,question,evidence_fingerprint]
    family='answer:'+hashlib.sha256(dumps(legacy).encode()).hexdigest()
    attempt=client.app.state.db.reserve(
        run['project_id'],run['id'],paid_task_key(family,0),Decimal('0.01'),
        settings.cheap_model,'legacy-request',settings.input_rate,settings.output_rate,
        interactive_question=True)
    response_data={
        'status':'ANSWERED','answer':'Use 2 inch Type L copper.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'Domestic water service pipe shall be 2 inch Type L copper.'}],
    }
    client.app.state.db.finalize_model_call(
        attempt,Decimal('0.01'),{'prompt_tokens':1,'completion_tokens':1},
        'legacy-question',response=response_data)

    result=ProjectQuestions(client.app.state.db,gateway).ask(run,question)

    assert result['cached'] is True and result['answer']=='Use 2 inch Type L copper.'
    assert requests==[]


def test_live_answer_rejects_a_quote_not_in_supplied_evidence(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    db = client.app.state.db
    bad = {'status': 'ANSWERED', 'answer': 'Use PVC.', 'source_findings': [], 'citations': [
        {'evidence_id': 'EV-QA-1', 'quote': 'Domestic water service pipe shall be PVC.'}]}
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            'id': 'question-bad-quote',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(bad)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
        }))))

    with pytest.raises(InvalidModelOutput, match='question response'):
        ProjectQuestions(db, gateway).ask(run, 'What is the water service pipe material?')

    call = db.one('SELECT state,response,error FROM model_calls WHERE run_id=?', (run['id'],))
    assert call['state'] == 'SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class'] == 'PROJECT_ANSWER'


def test_live_answer_settles_an_opposite_disposition_error_without_retry(
        client, project, tmp_path):
    source='Submittal 23-01 status: REJECTED.'
    run=_evidence(client,project,[source])
    db=client.app.state.db
    wrong={
        'status':'ANSWERED','answer':'Submittal 23-01 is approved.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json={
            'id':'question-opposite-disposition',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(wrong)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20},
        }))[1])))

    with pytest.raises(InvalidModelOutput,match='question response'):
        ProjectQuestions(db,gateway).ask(run,'What is the status of Submittal 23-01?')

    call=db.one('SELECT state,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1
    assert call['state']=='SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class']=='PROJECT_ANSWER'


def test_live_answer_settles_a_conflicting_disposition_error_without_retry(
        client, project, tmp_path):
    sources=['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.']
    run=_evidence(client,project,sources)
    db=client.app.state.db
    wrong={
        'status':'ANSWERED','answer':'Submittal 23-01 is rejected.',
        'source_findings':[],
        'citations':[{'evidence_id':f'EV-QA-{index}','quote':source}
                     for index,source in enumerate(sources,1)],
    }
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json={
            'id':'question-conflicting-disposition',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(wrong)}}],
            'usage':{'prompt_tokens':110,'completion_tokens':20},
        }))[1])))

    with pytest.raises(InvalidModelOutput,match='question response'):
        ProjectQuestions(db,gateway).ask(run,'What is the status of Submittal 23-01?')

    call=db.one('SELECT state,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1
    assert call['state']=='SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class']=='PROJECT_ANSWER'


def test_answer_rejects_a_numeric_claim_missing_from_its_citation(client, project):
    run = _evidence(client, project, [
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What is the water service pipe material and size?')
    wrong={
        'status':'ANSWERED','answer':'Use 4 inch Type L copper.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'Domestic water service pipe shall be 2 inch Type L copper.'}],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(wrong,evidence,'What is the water service pipe material and size?')


def test_numeric_grounding_accepts_commas_and_leading_zero_formatting(client, project):
    run = _evidence(client, project, [
        'RFI 0042 response requires concrete with 4,000 psi compressive strength.',
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What concrete compressive strength is required?')
    grounded={
        'status':'ANSWERED','answer':'RFI 42 requires 4000 psi concrete.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'RFI 0042 response requires concrete with 4,000 psi compressive strength.'}],
    }

    validate_answer_model(grounded,evidence,'What concrete compressive strength is required?')


def test_numeric_grounding_does_not_merge_comma_separated_values(client, project):
    run = _evidence(client, project, ['Grid coordinates are 1,2.'])
    evidence=retrieve_evidence(client.app.state.db,run,'What are the grid coordinates?')
    wrong={
        'status':'ANSWERED','answer':'The grid coordinate is 12.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':'Grid coordinates are 1,2.'}],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(wrong,evidence,'What are the grid coordinates?')


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 issued 2024-01-05. Due date 2024-02-10.',
     'RFI 42 is due 2024-01-10.'),
    ('RFI 42 was issued January 5, 2024. Due February 10, 2024.',
     'RFI 42 is due January 10, 2024.'),
])
def test_answer_rejects_a_date_recombined_from_cited_components(
        client, project, source, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    wrong={'status':'ANSWERED','answer':claim,'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='date'):
        validate_answer_model(wrong,evidence,'When is RFI 42 due?')


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 is due 2024-01-05.','RFI 0042 is due 2024-1-5.'),
    ('RFI 42 is due 2024-01-05.','RFI 42 is due January 5, 2024.'),
    ('RFI 42 is due January 5, 2024.','RFI 42 is due Jan. 5, 2024.'),
    ('RFI 42 is due 5 January 2024.','RFI 42 is due Jan 5 2024.'),
    ('RFI 42 is due 01/05/2024.','RFI 42 is due 1/5/2024.'),
])
def test_date_grounding_accepts_formatting_only_equivalence(client, project, source, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    grounded={'status':'ANSWERED','answer':claim,'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,'When is RFI 42 due?')


def test_date_grounding_does_not_interpret_an_ambiguous_slash_date(client, project):
    source='RFI 42 is due 01/05/2024.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    converted={'status':'ANSWERED','answer':'RFI 42 is due 2024-01-05.',
               'source_findings':[],
               'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='date'):
        validate_answer_model(converted,evidence,'When is RFI 42 due?')


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 issued 2024-01-05. Due date 2024-02-10.',
     'When is RFI 42 due?', 'RFI 42 is due 2024-01-05.'),
    ('Submittal 23-01 submitted 2024-03-01 and approved 2024-03-08.',
     'When was Submittal 23-01 approved?', 'Submittal 23-01 was approved 2024-03-01.'),
    ('Sent: January 5, 2024. Received: January 6, 2024.',
     'When was the email received?', 'The email was received January 5, 2024.'),
])
def test_answer_rejects_a_date_borrowed_from_another_role(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':claim,'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='date role'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 Due Date: 2024-02-10.',
     'When is RFI 42 due?', 'RFI 42 is due February 10, 2024.'),
    ('Submittal 23-01 submitted 2024-03-01 and approved 2024-03-08.',
     'When was Submittal 23-01 approved?', 'Submittal 23-01 was approved March 8, 2024.'),
    ('Sent: January 5, 2024. Received: January 6, 2024.',
     'When was the email received?', 'The email was received January 6, 2024.'),
    ('RFI 42: 2024-04-07 is the response date.',
     'What is the RFI 42 response date?', 'RFI 42 response date is April 7, 2024.'),
])
def test_date_role_grounding_accepts_the_same_labeled_date(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED','answer':claim,'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


def test_date_role_grounding_does_not_join_separate_citations(client, project):
    sources=['Due date:','2024-01-05']
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,'What is the due date in 2024?')
    combined={'status':'ANSWERED','answer':'The due date is 2024-01-05.',
              'source_findings':[],
              'citations':[{'evidence_id':f'EV-QA-{index}','quote':source}
                           for index,source in enumerate(sources,1)]}

    with pytest.raises(ValueError,match='date role'):
        validate_answer_model(combined,evidence,'What is the due date in 2024?')


def test_date_role_grounding_rejects_multiple_dates_for_the_same_role(client, project):
    source='Submittal 23-01 approved 2024-03-08. Approval Date: 2024-03-09.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'When was Submittal 23-01 approved?')
    selected={'status':'ANSWERED','answer':'Submittal 23-01 was approved 2024-03-09.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='multiple dates'):
        validate_answer_model(selected,evidence,'When was Submittal 23-01 approved?')


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 issued 2024-01-05. RFI 43 due 2024-02-10.',
     'When is RFI 42 due?', 'RFI 42 is due 2024-02-10.'),
    ('Submittal 23-01 issued 2024-01-05. Submittal 23-02 approved 2024-02-10.',
     'When was Submittal 23-01 approved?',
     'Submittal 23-01 was approved 2024-02-10.'),
])
def test_date_role_rejects_a_date_borrowed_from_another_workflow_identity(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':claim,'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow date role'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 due 2024-01-05. RFI 43 due 2024-02-10.',
     'When is RFI 42 due?', 'RFI 42 is due 2024-01-05.'),
    ('Submittal 23-01 approved 2024-01-05. Submittal 23-02 approved 2024-02-10.',
     'When was Submittal 23-01 approved?',
     'Submittal 23-01 was approved 2024-01-05.'),
])
def test_date_role_allows_independent_dates_for_distinct_workflow_identities(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED','answer':claim,'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


def test_date_role_uses_one_exact_workflow_identity_from_the_citation_locator(
        client, project):
    source='Due date: 2024-01-05.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    grounded={'status':'ANSWERED','answer':'RFI 42 is due January 5, 2024.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,'When is RFI 42 due?')


def test_date_role_rejects_an_unscoped_multi_workflow_locator(client, project):
    source='Due date: 2024-01-05.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    evidence[0]['locator']['section']='RFI 42 / RFI 43'
    wrong={'status':'ANSWERED','answer':'RFI 42 is due January 5, 2024.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow date role'):
        validate_answer_model(wrong,evidence,'When is RFI 42 due?')


def test_date_role_reuses_exact_numeric_rfi_normalization(client, project):
    source='Request for Information No. 0042 Due Date: 2024-01-05.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is RFI 42 due?')
    grounded={'status':'ANSWERED','answer':'RFI 42 is due January 5, 2024.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,'When is RFI 42 due?')


def test_status_question_still_rejects_submitted_and_approved_history(client, project):
    source='Submittal 23-01 submitted 2024-03-01 and approved 2024-03-08.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What is the status of Submittal 23-01?')
    selected={'status':'ANSWERED','answer':'Submittal 23-01 is approved.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='conflicting workflow dispositions'):
        validate_answer_model(selected,evidence,'What is the status of Submittal 23-01?')


def test_unlabeled_date_claim_remains_grounded_by_the_full_date(client, project):
    source='Project milestone 2024-01-05.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'When is the project milestone?')
    grounded={'status':'ANSWERED','answer':'The project milestone is January 5, 2024.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(grounded,evidence,'When is the project milestone?')


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 status: CLOSED.', 'What is the status of RFI 42?',
     'RFI 42 is open.'),
    ('Submittal 23-01 status: REJECTED.', 'What is the status of Submittal 23-01?',
     'Submittal 23-01 is approved.'),
    ('From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
     'What status does the email report?', 'The email reports an approved status.'),
])
def test_answer_rejects_a_workflow_disposition_opposite_to_its_citation(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 status: CLOSED.', 'What is the status of RFI 42?',
     'RFI 42 is resolved.'),
    ('Submittal 23-01 status: APPROVED AS NOTED.',
     'What is the status of Submittal 23-01?', 'Submittal 23-01 is approved.'),
    ('Submittal 23-01 status: NOT APPROVED.',
     'What is the status of Submittal 23-01?', 'Submittal 23-01 is rejected.'),
    ('From: architect@example.test\nSubject: Submittal review\nStatus: UNDER REVIEW.',
     'What status does the email report?', 'The email reports a pending status.'),
])
def test_workflow_disposition_grounding_accepts_bounded_equivalent_wording(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(grounded,evidence,question)


def test_disposition_grounding_does_not_invent_an_approval_qualifier(client, project):
    source='Submittal 23-01 status: APPROVED.'
    question='What is the status of Submittal 23-01?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    embellished={
        'status':'ANSWERED','answer':'Submittal 23-01 is approved as noted.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(embellished,evidence,question)


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 status: CLOSED.','RFI 42 is not closed.'),
    ('Submittal 23-01 status: REJECTED.','Submittal 23-01 is not rejected.'),
])
def test_disposition_grounding_does_not_drop_negation(
        client, project, source, claim):
    question='What is the workflow status?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 status: OPEN WAITING FOR SUBMISSION.',
     'RFI 42 is open and pending.'),
    ('RFI 42 status: CLOSED-DRAFT.','RFI 42 is closed and draft.'),
    ('Submittal 23-01 status: REVIEWED.','Submittal 23-01 was reviewed.'),
])
def test_disposition_grounding_covers_remaining_parser_status_boundaries(
        client, project, source, claim):
    question='What is the workflow status?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(grounded,evidence,question)


def test_disposition_grounding_uses_each_comparison_finding_own_citations(client, project):
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the status of RFI 42 and Submittal 23-01.')
    swapped={
        'status':'ANSWERED','answer':'The sources report different dispositions.','citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains a workflow disposition'):
        validate_answer_model(
            swapped,evidence,'Compare the status of RFI 42 and Submittal 23-01.')


@pytest.mark.parametrize(('sources','question','claim'),[
    (['RFI 42 status: OPEN.','RFI 42 status: CLOSED.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is rejected.'),
    (['Submittal 23-01 status: APPROVED.',
      'Submittal 23-01 status: APPROVED AS NOTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is approved as noted.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: SUBMITTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is pending.'),
    (['From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
      'From: contractor@example.test\nSubject: Submittal review\nStatus: APPROVED.'],
     'What status does the email report?','The email reports an approved status.'),
])
def test_answer_rejects_a_silent_choice_between_conflicting_cited_dispositions(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected={item['raw_text']:item['evidence_id'] for item in evidence}
    ambiguous={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected[source],'quote':source} for source in sources],
    }

    with pytest.raises(ValueError,match='conflicting'):
        validate_answer_model(ambiguous,evidence,question)


@pytest.mark.parametrize(('sources','question','claim'),[
    (['RFI 42 status: OPEN.','RFI 42 status: CLOSED.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is rejected.'),
    (['From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
      'From: contractor@example.test\nSubject: Submittal review\nStatus: APPROVED.'],
     'What status does the email report?','The email reports an approved status.'),
])
def test_answer_cannot_hide_a_conflicting_retrieved_disposition_by_omitting_its_citation(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected=next(item for item in evidence if item['raw_text']==sources[-1])
    hidden={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected['evidence_id'],'quote':sources[-1]}],
    }

    with pytest.raises(ValueError,match='conflicting'):
        validate_answer_model(hidden,evidence,question)


@pytest.mark.parametrize(('sources','question','claim'),[
    (['Request for Information No. 0042 status: CLOSED.','RFI 43 status: OPEN.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: APPROVED.','Submittal 23-02 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is approved.'),
])
def test_retrieved_disposition_conflicts_remain_isolated_by_exact_workflow_identity(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected=next(item for item in evidence if item['raw_text']==sources[0])
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected['evidence_id'],'quote':sources[0]}],
    }

    validate_answer_model(grounded,evidence,question)


def test_source_finding_cannot_hide_a_conflicting_retrieved_disposition(
        client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: OPEN.','RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    hidden={
        'status':'ANSWERED','answer':'The RFI is closed and the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-3',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    with pytest.raises(ValueError,match='retrieved source finding cites conflicting'):
        validate_answer_model(hidden,evidence,question)


def test_comparison_keeps_different_source_dispositions_isolated(client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    comparison={
        'status':'ANSWERED',
        'answer':'The RFI is closed while the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    validate_answer_model(comparison,evidence,question)


def test_comparison_uses_question_identity_when_a_finding_omits_the_number(
        client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('combined-rfis.txt',['RFI 42 status: CLOSED.','RFI 43 status: OPEN.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    comparison={
        'status':'ANSWERED','answer':'The RFI is closed while the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'combined-rfis.txt',
             'statement':'The RFI is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'The Submittal is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-3',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    validate_answer_model(comparison,evidence,question)


def test_comparison_answer_rejects_a_mixed_summary_without_each_source(client, project, tmp_path):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db = client.app.state.db
    incomplete = {
        'status': 'ANSWERED',
        'answer': 'The specification requires 2 inch pipe and RFI 42 changes it to 3 inch.',
        'source_findings': [{
            'source_type': 'SPECIFICATION',
            'file_name': 'project-spec.txt',
            'statement': 'The specification requires 2 inch Type L copper pipe.',
            'citations': [{
                'evidence_id': 'EV-SOURCE-1',
                'quote': 'Specification requires 2 inch Type L copper pipe.',
            }],
        }],
        'citations': [],
    }
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            'id': 'question-incomplete-comparison',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(incomplete)}}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 30},
        }))))

    with pytest.raises(InvalidModelOutput, match='question response'):
        ProjectQuestions(db, gateway).ask(
            run, 'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_answer_returns_each_source_with_inline_evidence(client, project, tmp_path):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db = client.app.state.db
    comparison = {
        'status': 'ANSWERED',
        'answer': 'RFI 42 increases the specified pipe size from 2 inches to 3 inches.',
        'citations': [],
        'source_findings': [
            {
                'source_type': 'SPECIFICATION',
                'file_name': 'project-spec.txt',
                'statement': 'The specification requires 2 inch Type L copper pipe.',
                'citations': [{
                    'evidence_id': 'EV-SOURCE-1',
                    'quote': 'Specification requires 2 inch Type L copper pipe.',
                }],
            },
            {
                'source_type': 'RFI',
                'file_name': 'RFI-42-response.txt',
                'statement': 'RFI 42 requires 3 inch Type L copper pipe.',
                'citations': [{
                    'evidence_id': 'EV-SOURCE-2',
                    'quote': 'RFI 42 response requires 3 inch Type L copper pipe.',
                }],
            },
        ],
    }
    requests=[]
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: (requests.append(request), httpx.Response(200, json={
            'id': 'question-valid-comparison',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(comparison)}}],
            'usage': {'prompt_tokens': 160, 'completion_tokens': 60},
        }))[1])))
    service=ProjectQuestions(db,gateway)

    result=service.ask(run,'Compare the specification and RFI 42 pipe requirements.')
    recovered=service.ask(run,'Compare the specification and RFI 42 pipe requirements.')

    assert result['citations']==[]
    assert [item['source_type'] for item in result['source_findings']]==['SPECIFICATION','RFI']
    assert result['source_findings'][1]['citations'][0]['quote'].startswith('RFI 42 response')
    assert recovered['cached'] is True and len(requests)==1


def test_comparison_source_type_must_match_the_cited_file(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the specification and RFI 42 pipe requirements.')
    mislabeled={
        'status':'ANSWERED','answer':'The sources differ.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'project-spec.txt','statement':'Two inch pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification requires 2 inch Type L copper pipe.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt','statement':'Three inch pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    with pytest.raises(ValueError,match='type does not match'):
        validate_answer_model(
            mislabeled,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_rejects_a_numeric_finding_missing_from_its_own_citation(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the specification and RFI 42 pipe requirements.')
    wrong={
        'status':'ANSWERED','answer':'The sources specify different pipe sizes.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires 4 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification requires 2 inch Type L copper pipe.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 requires 3 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(
            wrong,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_date_must_be_grounded_in_its_own_source_finding(client, project):
    run=_source_evidence(client,project,[
        ('project-spec.txt',['Specification issued 2024-01-05.']),
        ('RFI-42.txt',['RFI 42 is due 2024-02-10.']),
    ])
    question='Compare the specification date and RFI 42 due date.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The sources give different dates.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification was issued 2024-01-05.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification issued 2024-01-05.'}]},
            {'source_type':'RFI','file_name':'RFI-42.txt',
             'statement':'RFI 42 is due 2024-01-05.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 is due 2024-02-10.'}]},
        ],
    }

    with pytest.raises(ValueError,match='date'):
        validate_answer_model(wrong,evidence,question)


def test_comparison_date_role_must_be_grounded_in_its_own_source_finding(
        client, project):
    run=_source_evidence(client,project,[
        ('project-spec.txt',['Specification issued 2024-01-05.']),
        ('RFI-42.txt',['RFI 42 issued 2024-01-05. Due date 2024-02-10.']),
    ])
    question='Compare the specification date and RFI 42 due date.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The sources contain project dates.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification was issued 2024-01-05.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification issued 2024-01-05.'}]},
            {'source_type':'RFI','file_name':'RFI-42.txt',
             'statement':'RFI 42 is due 2024-01-05.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 issued 2024-01-05. Due date 2024-02-10.'}]},
        ],
    }

    with pytest.raises(ValueError,match='date role'):
        validate_answer_model(wrong,evidence,question)


def test_comparison_date_role_cannot_borrow_another_workflow_identity(
        client, project):
    run=_source_evidence(client,project,[
        ('project-spec.txt',['Specification issued 2024-01-05.']),
        ('RFI-log.txt',['RFI 42 issued 2024-01-05. RFI 43 due 2024-02-10.']),
    ])
    question='Compare the specification date and RFI 42 due date.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The sources contain project dates.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification was issued 2024-01-05.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification issued 2024-01-05.'}]},
            {'source_type':'RFI','file_name':'RFI-log.txt',
             'statement':'RFI 42 is due 2024-02-10.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 issued 2024-01-05. RFI 43 due 2024-02-10.'}]},
        ],
    }

    with pytest.raises(ValueError,match='workflow date role'):
        validate_answer_model(wrong,evidence,question)


def test_answer_rejects_a_workflow_identifier_assembled_from_unrelated_numbers(
        client, project):
    source='RFI 42 response: Use Type L copper. Coordination item 43 remains open.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What does RFI 42 require for the pipe material?')
    wrong={
        'status':'ANSWERED','answer':'RFI 43 requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow identifier'):
        validate_answer_model(
            wrong,evidence,'What does RFI 42 require for the pipe material?')


@pytest.mark.parametrize(('source_role', 'claimed_role'), [
    ('response', 'question'),
    ('question', 'response'),
])
def test_answer_rejects_an_rfi_question_response_role_swap(
        client, project, source_role, claimed_role):
    source=f'RFI 42 {source_role}: Use Type L copper.'
    run=_evidence(client,project,[source])
    question=f'What does the RFI 42 {source_role} require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']=f'RFI 42 > {source_role.upper()}'
    wrong={
        'status':'ANSWERED','answer':f'RFI 42 {claimed_role} requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow role'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','locator_section','claim'),[
    ('RFI 42 official response: Use Type L copper.','Section 1',
     'RFI 42 official response requires Type L copper.'),
    ('RFI 42 response: Use Type L copper.','Section 1',
     'RFI 42 answer requires Type L copper.'),
    ('Use Type L copper.','RFI 42 > RESPONSE',
     'RFI 42 reply requires Type L copper.'),
    ('RFI 42 question: May Type L copper be used?','Section 1',
     'RFI 42 question asks whether Type L copper may be used.'),
])
def test_answer_accepts_an_rfi_role_from_its_quote_or_exact_locator(
        client, project, source, locator_section, claim):
    run=_evidence(client,project,[source])
    question='What does the source say about Type L copper?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']=locator_section
    answer={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


def test_multi_identity_locator_cannot_donate_an_rfi_role(client, project):
    source='Use Type L copper.'
    run=_evidence(client,project,[source])
    question='What does RFI 42 require?'
    evidence=retrieve_evidence(
        client.app.state.db,run,'What does the source say about Type L copper?')
    evidence[0]['locator']['section']='RFI 42 > QUESTION / RFI 43 > RESPONSE'
    wrong={
        'status':'ANSWERED','answer':'RFI 42 response requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow role'):
        validate_answer_model(wrong,evidence,question)


def test_one_statement_cannot_bypass_rfi_role_grounding_with_two_identities(
        client, project):
    response='RFI 42 response: Use Type L copper.'
    question_source='RFI 43 question: May PVC be used?'
    run=_evidence(client,project,[response,question_source])
    question='Summarize the RFI 42 response and RFI 43 question.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED',
        'answer':'RFI 42 question differs from the RFI 43 response.',
        'source_findings':[],
        'citations':[
            {'evidence_id':'EV-QA-1','quote':response},
            {'evidence_id':'EV-QA-2','quote':question_source},
        ],
    }

    with pytest.raises(ValueError,match='workflow role'):
        validate_answer_model(wrong,evidence,question)

    correct={**wrong,
             'answer':'RFI 42 response differs from the RFI 43 question.'}
    validate_answer_model(correct,evidence,question)


def test_source_finding_rejects_an_rfi_role_swap(client, project):
    source='RFI 42 question: May Type L copper be used?'
    run=_source_evidence(client,project,[('RFI-42.txt',[source])])
    question='What does the RFI 42 question say?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The RFI record addresses Type L copper.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42.txt',
             'statement':'RFI 42 response requires Type L copper.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]},
        ],
    }

    with pytest.raises(ValueError,match='workflow role'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize('source',[
    'RFI 42 response time is 5 days. Use Type L copper.',
    'RFI 42 official response date is 2024-01-05. Use Type L copper.',
])
def test_rfi_timing_labels_do_not_ground_a_response_role(client, project, source):
    run=_evidence(client,project,[source])
    question='What does RFI 42 require for Type L copper?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'RFI 42 reply requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow role'):
        validate_answer_model(wrong,evidence,question)


def test_email_evidence_can_ground_an_embedded_rfi_response_role(client, project):
    source=('From: designer@example.com\nSubject: RFI 42 reply\n'
            'RFI 42 response: Use Type L copper.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What does the RFI 42 response require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={
        'status':'ANSWERED','answer':'RFI 42 reply requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


@pytest.mark.parametrize(('text','expected'),[
    ('Request for Information No. 0042 status: CLOSED.',{('RFI','42')}),
    ('RFI 42 is open; RFI 43 is closed.',{('RFI','42'),('RFI','43')}),
    ('RFI-ARC-0042 response.',{('RFI','ARC-0042')}),
    ('Submittal 23-01 and Submission 23 05 00 - 01.',
     {('SUBMITTAL','23-01'),('SUBMITTAL','23 05 00-01')}),
])
def test_workflow_identity_parser_preserves_exact_parser_boundaries(text,expected):
    assert _workflow_identities(text)==expected


@pytest.mark.parametrize(('source','locator_section'),[
    ('Request for Information No. 0042 response: Use Type L copper.','Section 1'),
    ('Pipe material is Type L copper.','RFI 42 > RESPONSE'),
])
def test_answer_accepts_a_workflow_identifier_in_its_quote_or_locator(
        client, project, source, locator_section):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What does RFI 42 require for the pipe material?')
    evidence[0]['locator']['section']=locator_section
    answer={
        'status':'ANSWERED','answer':'RFI 42 requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(
        answer,evidence,'What does RFI 42 require for the pipe material?')


@pytest.mark.parametrize('claimed_size',[4,42])
def test_locator_grounded_workflow_id_does_not_ground_another_number(
        client, project, claimed_size):
    source='Pipe size shall be 2 inch Type L copper.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What pipe size does RFI 42 require?')
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    wrong={
        'status':'ANSWERED','answer':f'RFI 42 requires {claimed_size} inch Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(wrong,evidence,'What pipe size does RFI 42 require?')


def test_answer_rejects_a_number_borrowed_from_another_rfi(client, project):
    source='RFI 42 response: Use 2 inch pipe. RFI 43 response: Use 4 inch pipe.'
    run=_evidence(client,project,[source])
    question='What pipe size does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'RFI 42 requires 4 inch pipe.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow numeric'):
        validate_answer_model(wrong,evidence,question)


def test_answer_accepts_numbers_scoped_to_their_own_rfis(client, project):
    source='RFI 42 response: Use 2 inch pipe. RFI 43 response: Use 4 inch pipe.'
    run=_evidence(client,project,[source])
    question='What pipe size does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={
        'status':'ANSWERED','answer':'RFI 42 requires 2 inch pipe.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


@pytest.mark.parametrize(('locator_section','accepted'),[
    ('RFI 0042 > RESPONSE',True),
    ('RFI 42 / RFI 43',False),
])
def test_locator_scopes_a_number_only_with_one_exact_rfi(
        client, project, locator_section, accepted):
    source='Pipe size shall be 2 inch Type L copper.'
    run=_evidence(client,project,[source])
    question='What pipe size does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']=locator_section
    answer={
        'status':'ANSWERED','answer':'RFI 42 requires 2 inch Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    if accepted:
        validate_answer_model(answer,evidence,question)
    else:
        with pytest.raises(ValueError,match='workflow numeric'):
            validate_answer_model(answer,evidence,question)


def test_source_finding_rejects_a_number_borrowed_from_another_submittal(
        client, project):
    spec='Specification requires Type L copper pipe.'
    submittals=('Submittal 23-01 requires 2 inch pipe. '
                'Submittal 23-02 requires 4 inch pipe.')
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittals.txt',[submittals])])
    question='Compare the specification and Submittal 23-01 pipe requirements.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The sources describe Type L copper pipe.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittals.txt',
             'statement':'Submittal 23-01 requires 4 inch pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittals}]},
        ],
    }

    with pytest.raises(ValueError,match='workflow numeric'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_identifier_number_is_not_a_property_value(client, project):
    source='RFI 42 response: Use Type L copper pipe.'
    run=_evidence(client,project,[source])
    question='What pipe material does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={
        'status':'ANSWERED','answer':'RFI 42 requires Type L copper pipe.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


def test_answer_rejects_insulation_thickness_as_pipe_diameter(client, project):
    source=('RFI 42 response: Pipe diameter is 2 inch. '
            'Insulation thickness is 1 inch.')
    run=_evidence(client,project,[source])
    question='What pipe diameter does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'RFI 42 requires a 1 inch pipe diameter.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='measurement property'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize('answer_text',[
    'RFI 42 requires a 2 inch pipe diameter.',
    'RFI 42 requires 1 inch insulation thickness.',
])
def test_answer_accepts_each_number_with_its_own_measurement_property(
        client, project, answer_text):
    source=('RFI 42 response: Pipe diameter is 2 inch. '
            'Insulation thickness is 1 inch.')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'What dimensions does RFI 42 require?')
    answer={'status':'ANSWERED','answer':answer_text,'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'What dimensions does RFI 42 require?')


@pytest.mark.parametrize('source',[
    'RFI 42 response: Domestic water service pipe shall be 2 inch Type L copper.',
    'RFI 42 response: Use 2-inch Type L copper pipe.',
    'RFI 42 response: Use 2" Type L copper pipe.',
])
def test_plain_dimensional_pipe_wording_still_supports_pipe_size(
        client, project, source):
    run=_evidence(client,project,[source])
    question='What pipe size does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED','answer':'RFI 42 requires a 2 inch pipe size.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


def test_source_finding_rejects_submittal_height_as_width(client, project):
    spec='Specification requires listed equipment dimensions.'
    submittal='Submittal 23-01 equipment width is 24 inch. Height is 36 inch.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 equipment dimensions.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources describe equipment dimensions.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires listed equipment dimensions.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 equipment width is 36 inch.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='measurement property'):
        validate_answer_model(wrong,evidence,question)


def test_specification_answer_rejects_height_as_width(client, project):
    source='Equipment width is 24 inch. Height is 36 inch.'
    run=_evidence(client,project,[source])
    question='What equipment width does the specification require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'The equipment width is 36 inch.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='measurement property'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_measurement_property_cannot_borrow_another_rfi(client, project):
    source=('RFI 42 response: Pipe diameter is 2 inch. '
            'RFI 43 response: Insulation thickness is 2 inch.')
    run=_evidence(client,project,[source])
    question='What insulation thickness does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 requires 2 inch insulation thickness.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow measurement property'):
        validate_answer_model(wrong,evidence,question)


def test_email_answer_rejects_pressure_as_compressive_strength(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'High pressure is 150 psi. Compressive strength is 4,000 psi.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    question='What compressive strength does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 requires 150 psi compressive strength.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='measurement property'):
        validate_answer_model(wrong,evidence,question)


def test_answer_rejects_insulation_unit_as_pipe_diameter_unit(client, project):
    source=('RFI 42 response: Pipe diameter is 2 inch. '
            'Insulation thickness is 2 mm.')
    run=_evidence(client,project,[source])
    question='What pipe diameter does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 requires a 2 mm pipe diameter.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='property unit'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source_unit','answer_unit'),[
    ('inch','inches'),('in.','"'),('"','in.'),('feet','ft'),
])
def test_answer_accepts_formatting_equivalent_measurement_units(
        client, project, source_unit, answer_unit):
    source=f'RFI 42 response: Pipe diameter is 2 {source_unit}.'
    run=_evidence(client,project,[source])
    question='What pipe diameter does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED',
            'answer':f'RFI 42 requires a 2 {answer_unit} pipe diameter.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


@pytest.mark.parametrize(('property_name','source_value','answer_value'),[
    ('pipe diameter','2mm','2 mm'),
    ('compressive strength','150MPa','150 MPa'),
])
def test_answer_accepts_compact_recognized_measurement_units(
        client, project, property_name, source_value, answer_value):
    source=f'RFI 42 response: {property_name.title()} is {source_value}.'
    run=_evidence(client,project,[source])
    question=f'What {property_name} does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED',
            'answer':f'RFI 42 requires {answer_value} {property_name}.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


def test_compact_measurement_units_are_allowlisted():
    assert _numeric_unit_values('Thickness 2mm; strength 150MPa; flow 4L/s.')=={
        ('2','MM'),('150','MPA'),('4','LPS')}
    assert _numeric_unit_values('Model 2model; speed 2m/s.')==set()


def test_answer_rejects_recombined_specification_section(client, project):
    source=('RFI 42 response: Specification Section 22 11 16 covers piping. '
            'Specification Section 23 05 00 covers HVAC.')
    run=_evidence(client,project,[source])
    question='Which section applies?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED',
           'answer':'RFI 42 references Specification Section 22 05 16.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='specification section'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source_section','answer_section'),[
    ('22 11 16','22-11-16'),('22-11-16','22.11.16'),('22.11.16','22 11 16'),
])
def test_answer_accepts_formatting_equivalent_specification_sections(
        client, project, source_section, answer_section):
    source=f'RFI 42 response: Specification Section {source_section} covers piping.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which section applies?')
    answer={'status':'ANSWERED',
            'answer':f'RFI 42 references Specification Section {answer_section}.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which section applies?')


def test_specification_section_can_use_one_exact_evidence_locator(client, project):
    source='Use Type L copper for the domestic water service.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which section applies?')
    evidence[0]['locator']['section']='RFI 42 > RESPONSE > Section 22 11 16'
    answer={'status':'ANSWERED',
            'answer':'RFI 42 references Specification Section 22 11 16.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which section applies?')


def test_specification_section_cannot_be_borrowed_from_another_rfi(client, project):
    source=('RFI 42 response: Specification Section 22 11 16 applies. '
            'RFI 43 response: Specification Section 23 05 00 applies.')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which section applies?')
    wrong={'status':'ANSWERED',
           'answer':'RFI 42 references Specification Section 23 05 00.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow specification section'):
        validate_answer_model(wrong,evidence,'Which section applies?')


def test_email_answer_rejects_recombined_specification_section(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'Use Specification Section 22 11 16. '
            'Coordinate Specification Section 23 05 00.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Which section applies?')
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED',
           'answer':'RFI 42 references Specification Section 22 05 16.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='specification section'):
        validate_answer_model(wrong,evidence,'Which section applies?')


def test_submittal_finding_cannot_borrow_specification_section(
        client, project):
    spec='Specification Section 22 11 16 requires Type L copper.'
    submittal='Submittal 23-01 references Specification Section 23 05 00.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 specification sections.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources reference different sections.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':spec,
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':('Submittal 23-01 references Specification '
                          'Section 22 11 16.'),
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='specification section'):
        validate_answer_model(wrong,evidence,question)


def test_specification_section_parser_does_not_treat_submittal_id_as_section():
    assert _spec_section_values('Submittal 23 05 00 - 01 is pending.')==set()
    assert _spec_section_values('Specification Section 23 05 00.13 applies.')=={
        '230500.13'}


def test_revision_label_cannot_be_borrowed_from_another_rfi(client, project):
    source=('RFI 42 response: Drawing P2.01 Revision A applies. '
            'RFI 43 response: Drawing P2.01 Revision B applies.')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which drawing revision applies?')
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Drawing P2.01 Revision B.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow revision label'):
        validate_answer_model(wrong,evidence,'Which drawing revision applies?')


def test_answer_rejects_recombined_revision_label(client, project):
    source='RFI 42 response: Revision: A1 superseded Revision: B2.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which revision applies?')
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Revision A2.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='revision label'):
        validate_answer_model(wrong,evidence,'Which revision applies?')


@pytest.mark.parametrize(('source_revision','answer_revision'),[
    ('Revision A','Rev. A'),('Revision: IFC','Rev IFC'),
    ('Revision No. P2','Rev: P2'),
])
def test_answer_accepts_formatting_equivalent_revision_labels(
        client, project, source_revision, answer_revision):
    source=f'RFI 42 response: Drawing P2.01 {source_revision} applies.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which drawing revision applies?')
    answer={'status':'ANSWERED',
            'answer':f'RFI 42 uses Drawing P2.01 {answer_revision}.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which drawing revision applies?')


def test_numeric_revision_label_is_not_treated_as_a_quantity(client, project):
    source='RFI 42 response: Source document Revision 2 applies.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which drawing revision applies?')
    answer={'status':'ANSWERED','answer':'RFI 42 uses Revision 2.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which drawing revision applies?')


def test_email_revision_label_stays_with_its_workflow(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'RFI 42 response: Drawing Revision A applies. '
            'Submittal 23-01: Drawing Revision B applies.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Which drawing revision applies?')
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Drawing Revision B.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow revision label'):
        validate_answer_model(wrong,evidence,'Which drawing revision applies?')


def test_submittal_finding_cannot_borrow_specification_revision(client, project):
    spec='Specification drawing list requires Revision A.'
    submittal='Submittal 23-01 product data is based on Revision B.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 drawing revisions.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources identify different revisions.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':spec,
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 product data is based on Revision A.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='revision label'):
        validate_answer_model(wrong,evidence,question)


def test_revision_label_parser_ignores_dates_and_unlabelled_status_words():
    assert _revision_label_values(
        'Revision date: 2024-01-05. Revision history is current.')==set()
    assert _revision_label_values('Revision: 2024-01-05')==set()
    assert _revision_label_values('Revision AS BUILT; Rev. IFC; Revision No. P2')=={
        'AS BUILT','IFC','P2'}


def test_answer_rejects_recombined_drawing_identifier(client, project):
    source='RFI 42 response: Coordinate Sheet A1.01 with Sheet P2.02.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which sheet applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Sheet A1.02.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='drawing identifier'):
        validate_answer_model(wrong,evidence,'Which sheet applies to RFI 42?')


def test_answer_rejects_recombined_detail_callout(client, project):
    source='RFI 42 response: Coordinate Detail 3/A5.1 with Detail 5/A7.2.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which detail applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Detail 3/A7.2.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='drawing identifier'):
        validate_answer_model(wrong,evidence,'Which detail applies to RFI 42?')


def test_drawing_identifier_cannot_be_borrowed_from_another_rfi(client, project):
    source=('RFI 42 response: Use Sheet A1.01. '
            'RFI 43 response: Use Drawing P2.02.')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which sheet applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Dwg. P2.02.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow drawing identifier'):
        validate_answer_model(wrong,evidence,'Which sheet applies to RFI 42?')


@pytest.mark.parametrize(('source_identifier','answer_identifier'),[
    ('Sheet A1.01','Drawing A1.01'),
    ('Drawing No. P2-02','Dwg: P2-02'),
    ('Dwg. M–101','Sheet M-101'),
    ('Detail No. 3/A5.1','Detail: 3/A5.1'),
])
def test_answer_accepts_prefix_equivalent_drawing_identifiers(
        client, project, source_identifier, answer_identifier):
    source=f'RFI 42 response: Use {source_identifier}.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which sheet applies to RFI 42?')
    answer={'status':'ANSWERED','answer':f'RFI 42 uses {answer_identifier}.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which sheet applies to RFI 42?')


def test_numeric_drawing_identifier_is_not_treated_as_a_quantity(client, project):
    source='RFI 42 response: Use Sheet 2.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which sheet applies to RFI 42?')
    answer={'status':'ANSWERED','answer':'RFI 42 uses Drawing 2.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which sheet applies to RFI 42?')


def test_sheet_locator_can_ground_only_its_exact_drawing_identifier(client, project):
    source='Mechanical equipment schedule.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'What is in the equipment schedule?')
    evidence[0]['locator']['sheet']='M1.1'
    grounded={'status':'ANSWERED','answer':'Sheet M1.1 contains the mechanical equipment schedule.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}
    invented={**grounded,'answer':'Sheet M1.2 contains the mechanical equipment schedule.'}

    validate_answer_model(grounded,evidence,'What is shown on Sheet M1.1?')
    with pytest.raises(ValueError,match='drawing identifier'):
        validate_answer_model(invented,evidence,'What is shown on Sheet M1.1?')


def test_email_drawing_identifier_stays_with_its_workflow(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'RFI 42 response: Use Sheet A1.01. '
            'Submittal 23-01: Use Sheet P2.02.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Which sheet applies to RFI 42?')
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Sheet P2.02.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow drawing identifier'):
        validate_answer_model(wrong,evidence,'Which sheet applies to RFI 42?')


def test_email_detail_callout_stays_with_its_workflow(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'RFI 42 response: Use Detail 3/A5.1. '
            'Submittal 23-01: Use Detail 5/A7.2.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Which detail applies to RFI 42?')
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 uses Detail 5/A7.2.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow drawing identifier'):
        validate_answer_model(wrong,evidence,'Which detail applies to RFI 42?')


def test_submittal_finding_cannot_borrow_specification_drawing_identifier(
        client, project):
    spec='Specification drawing list requires Sheet A1.01.'
    submittal='Submittal 23-01 product data references Sheet P2.02.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 drawing references.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources reference different drawings.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':spec,
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 product data references Sheet A1.01.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='drawing identifier'):
        validate_answer_model(wrong,evidence,question)


def test_drawing_identifier_parser_requires_an_explicit_digit_bearing_label():
    assert _drawing_identifier_values(
        'Drawing revision A; drawing list; sheet status current.')==set()
    assert _drawing_identifier_values('Drawing 2024-01-05')==set()
    assert _drawing_identifier_values(
        'Sheet A1.01; Drawing No. P2-02; Dwg: M–101; Detail 3/A5.1')=={
            'A1.01','P2-02','M-101','3/A5.1'}


def test_answer_rejects_recombined_clause_identifier(client, project):
    source='RFI 42 response: Follow Paragraph 2.3.1 and Paragraph 4.5.6.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which paragraph applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 requires Paragraph 2.3.6.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='clause identifier'):
        validate_answer_model(wrong,evidence,'Which paragraph applies to RFI 42?')


def test_clause_identifier_cannot_be_borrowed_from_another_rfi(client, project):
    source=('RFI 42 response: Follow Clause 2.3.1. '
            'RFI 43 response: Follow Clause 2.3.2.')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which clause applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 requires Clause 2.3.2.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow clause identifier'):
        validate_answer_model(wrong,evidence,'Which clause applies to RFI 42?')


@pytest.mark.parametrize(('source_identifier','answer_identifier'),[
    ('Paragraph 2.3.1','Para. 2.3.1'),
    ('Clause No. 4.5.6','Clause: 4.5.6'),
    ('Article 1.2A','Article No. 1.2a'),
])
def test_answer_accepts_formatting_equivalent_clause_identifiers(
        client, project, source_identifier, answer_identifier):
    source=f'RFI 42 response: Follow {source_identifier}.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which clause applies to RFI 42?')
    answer={'status':'ANSWERED','answer':f'RFI 42 requires {answer_identifier}.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Which clause applies to RFI 42?')


def test_clause_identifier_types_are_not_interchangeable(client, project):
    source='RFI 42 response: Follow Paragraph 2.3.1.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which paragraph applies to RFI 42?')
    wrong={'status':'ANSWERED','answer':'RFI 42 requires Article 2.3.1.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='clause identifier'):
        validate_answer_model(wrong,evidence,'Which paragraph applies to RFI 42?')


def test_paragraph_locator_can_ground_only_its_exact_identifier(client, project):
    source='Provide the specified hydrostatic test.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Which test is specified?')
    evidence[0]['locator']['paragraph']='2.3.1'
    grounded={'status':'ANSWERED','answer':'Paragraph 2.3.1 requires the hydrostatic test.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}
    invented={**grounded,'answer':'Paragraph 2.3.2 requires the hydrostatic test.'}

    validate_answer_model(grounded,evidence,'What does Paragraph 2.3.1 require?')
    with pytest.raises(ValueError,match='clause identifier'):
        validate_answer_model(invented,evidence,'What does Paragraph 2.3.1 require?')


def test_email_clause_identifier_stays_with_its_workflow(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'RFI 42 response: Follow Clause 2.3.1. '
            'Submittal 23-01: Follow Clause 2.3.2.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Which clause applies to RFI 42?')
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 requires Clause 2.3.2.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow clause identifier'):
        validate_answer_model(wrong,evidence,'Which clause applies to RFI 42?')


def test_submittal_finding_cannot_borrow_specification_clause_identifier(
        client, project):
    spec='Specification Paragraph 2.3.1 requires hydrostatic testing.'
    submittal='Submittal 23-01 references Paragraph 4.5.6.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 paragraph references.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources reference different paragraphs.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':spec,
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 references Paragraph 2.3.1.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='clause identifier'):
        validate_answer_model(wrong,evidence,question)


def test_clause_identifier_parser_requires_an_explicit_dotted_label():
    assert _clause_identifier_values(
        'Unlabelled 2.3.1; Paragraph status current; Clause 7.')==set()
    assert _clause_identifier_values('Paragraph 2024.01.05')==set()
    assert _clause_identifier_values(
        'Paragraph 2.3.1; Para. 2.3.1; Clause No. 4.5.6; Article: 1.2a')=={
            'PARAGRAPH:2.3.1','CLAUSE:4.5.6','ARTICLE:1.2A'}


def test_answer_rejects_an_email_subject_absent_from_its_citation(client, project):
    source=('From: architect@example.test\n'
            'Subject: RFI 42 Domestic Water Pipe\n'
            'Use Type L copper.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'Email subject: RFI 42 Fire Alarm Coordination.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    with pytest.raises(ValueError,match='email subject'):
        validate_answer_model(wrong,evidence,question)


def test_answer_accepts_an_exact_email_subject_with_formatting_only_changes(
        client, project):
    source=('From: architect@example.test\n'
            'Subject: RFI 42: Domestic   Water Pipe\n'
            'Use Type L copper.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={
        'status':'ANSWERED',
        'answer':'Subject line is "rfi 42: domestic water pipe".',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


def test_answer_accepts_a_quoted_email_subject_containing_sentence_punctuation(
        client, project):
    source=('From: architect@example.test\n'
            'Subject: RFI 42. Domestic Water Pipe\n'
            'Use Type L copper.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={
        'status':'ANSWERED',
        'answer':'Email subject: "RFI 42. Domestic Water Pipe".',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    validate_answer_model(answer,evidence,question)


def test_quoted_email_subject_preserves_its_own_terminal_punctuation(client, project):
    source=('From: architect@example.test\n'
            'Subject: Can Type L copper be used?\n'
            'RFI 42 question follows.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':'Email subject: "Can Type L copper be used?".',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }
    changed={**grounded,'answer':'Email subject: "Can Type L copper be used".'}

    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='email subject absent'):
        validate_answer_model(changed,evidence,question)


def test_email_subject_question_requires_an_explicit_subject_claim(client, project):
    source=('From: architect@example.test\n'
            'Subject: RFI 42 Domestic Water Pipe\n'
            'Use Type L copper.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evasive={
        'status':'ANSWERED','answer':'The email concerns RFI 42 Domestic Water Pipe.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    with pytest.raises(ValueError,match='requires an explicit email subject'):
        validate_answer_model(evasive,evidence,question)


def test_answer_rejects_an_email_subject_recombined_from_two_headers(
        client, project):
    source=('From: architect@example.test\n'
            'Subject: RFI 42 Domestic Water Pipe\n'
            'Subject: RFI 43 Fire Alarm Coordination')
    run=_source_evidence(client,project,[('thread.eml',[source])])
    question='What is the email subject?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'Email subject: RFI 42 Fire Alarm Coordination.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    with pytest.raises(ValueError,match='email subject absent'):
        validate_answer_model(wrong,evidence,question)


def test_email_source_finding_cannot_borrow_another_email_subject(client, project):
    first='From: architect@example.test\nSubject: Domestic Water Pipe'
    second='From: engineer@example.test\nSubject: Fire Alarm Coordination'
    run=_source_evidence(client,project,[
        ('water.eml',[first]),('fire-alarm.eml',[second])])
    question='Compare the email subjects for Domestic Water Pipe and Fire Alarm Coordination.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The emails have different subjects.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'water.eml',
             'statement':'Email subject: Fire Alarm Coordination.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':first}]},
            {'source_type':'EMAIL','file_name':'fire-alarm.eml',
             'statement':'Email subject: Fire Alarm Coordination.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':second}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains an email subject'):
        validate_answer_model(wrong,evidence,question)


def test_email_subject_comparison_requires_a_subject_claim(client, project):
    first='From: architect@example.test\nSubject: Domestic Water Pipe'
    second='From: engineer@example.test\nSubject: Fire Alarm Coordination'
    run=_source_evidence(client,project,[
        ('water.eml',[first]),('fire-alarm.eml',[second])])
    question='Compare the email subjects for Domestic Water Pipe and Fire Alarm Coordination.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    vague={
        'status':'ANSWERED','answer':'The emails concern different work.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'water.eml',
             'statement':'The first email concerns domestic water pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':first}]},
            {'source_type':'EMAIL','file_name':'fire-alarm.eml',
             'statement':'The second email concerns fire alarm coordination.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':second}]},
        ],
    }

    with pytest.raises(ValueError,match='requires an explicit email subject'):
        validate_answer_model(vague,evidence,question)


@pytest.mark.parametrize(('source','file_name'),[
    ('RFI 42\nSubject: Domestic Water Pipe','RFI-42.txt'),
    ('Submittal 23-01\nSubject: Domestic Water Pipe','Submittal-23-01.txt'),
])
def test_workflow_form_subject_does_not_ground_an_email_subject(
        client, project, source, file_name):
    run=_source_evidence(client,project,[(file_name,[source])])
    question='What is the email subject for Domestic Water Pipe?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'Email subject: Domestic Water Pipe.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}],
    }

    with pytest.raises(ValueError,match='email subject absent'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','file_name','question','answer'),[
    ('RFI 42\nSubject: Domestic Water Pipe\nQuestion: Can Type L copper be used?',
     'RFI-42.txt','What is the Subject line of RFI 42?',
     'RFI 0042 subject: “domestic   water pipe”.'),
    ('Submittal 23-01\nSubject: Domestic Water Pipe\nStatus: PENDING',
     'Submittal-23-01.txt','What is the subject of Submittal 23-01?',
     'Submittal 23-01 subject: Domestic Water Pipe.'),
])
def test_workflow_subject_accepts_the_same_scoped_form_value(
        client, project, source, file_name, question, answer):
    run=_source_evidence(client,project,[(file_name,[source])])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED','answer':answer,'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


def test_rfi_subject_cannot_borrow_another_rfi_subject(client, project):
    source=('RFI 42\nSubject: Domestic Water Pipe\nQuestion: Use Type L copper?\n'
            'RFI 43\nSubject: Fire Alarm Coordination\nQuestion: Revise devices?')
    run=_source_evidence(client,project,[('RFI-log.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 subject: Fire Alarm Coordination.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow subject absent'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_subject_cannot_be_recombined(client, project):
    source=('RFI 42\nSubject: Domestic Water Pipe\n'
            'RFI 43\nSubject: Fire Alarm Coordination')
    run=_source_evidence(client,project,[('RFI-log.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 subject: Domestic Alarm Coordination.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow subject absent'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_subject_question_requires_an_explicit_scoped_subject(client, project):
    source='RFI 42\nSubject: Domestic Water Pipe\nQuestion: Use Type L copper?'
    run=_source_evidence(client,project,[('RFI-42.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evasive={'status':'ANSWERED','answer':'RFI 42 concerns domestic water.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires an explicit scoped subject'):
        validate_answer_model(evasive,evidence,question)


def test_workflow_source_finding_cannot_borrow_another_subject(client, project):
    rfi='RFI 42\nSubject: Domestic Water Pipe\nQuestion: Use Type L copper?'
    submittal='Submittal 23-01\nSubject: Fire Alarm Devices\nStatus: PENDING'
    run=_source_evidence(client,project,[
        ('RFI-42.txt',[rfi]),('Submittal-23-01.txt',[submittal])])
    question='Compare the subjects of RFI 42 and Submittal 23-01.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The workflow subjects differ.','citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42.txt',
             'statement':'RFI 42 subject: Domestic Water Pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':rfi}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 subject: Domestic Water Pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains a workflow subject'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_subject_uses_one_exact_identity_from_the_citation_locator(
        client, project):
    source='Subject: Domestic Water Pipe'
    run=_source_evidence(client,project,[('RFI-42.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > FORM'
    grounded={'status':'ANSWERED','answer':'RFI 42 subject: Domestic Water Pipe.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


def test_workflow_subject_rejects_an_unscoped_multi_identity_locator(client, project):
    source='Subject: Domestic Water Pipe'
    run=_source_evidence(client,project,[('RFI-log.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 / RFI 43'
    wrong={'status':'ANSWERED','answer':'RFI 42 subject: Domestic Water Pipe.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow subject absent'):
        validate_answer_model(wrong,evidence,question)


def test_email_subject_header_does_not_ground_an_rfi_form_subject(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Subject: RFI 42 Domestic Water Pipe')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 subject: Domestic Water Pipe.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow subject absent'):
        validate_answer_model(wrong,evidence,question)


def test_quoted_workflow_subject_preserves_terminal_punctuation(client, project):
    source='RFI 42\nSubject: Can Type L copper be used?\nQuestion: Confirm material.'
    run=_source_evidence(client,project,[('RFI-42.txt',[source])])
    question='What is the subject of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED',
              'answer':'RFI 42 subject: “Can Type L copper be used?”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}
    changed={**grounded,'answer':'RFI 42 subject: “Can Type L copper be used”.'}

    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='workflow subject absent'):
        validate_answer_model(changed,evidence,question)


def test_workflow_subject_parser_keeps_each_nearest_preceding_identity():
    source=('RFI 42\nSubject: Domestic Water Pipe\n'
            'RFI 43\nSubject: Fire Alarm Coordination')

    assert _workflow_subject_source_values(source)=={
        (('RFI','42'),'domestic water pipe'),
        (('RFI','43'),'fire alarm coordination')}
    assert _workflow_subject_claim_values(
        'RFI 42 subject: Domestic Water Pipe.')=={
            (('RFI','42'),'domestic water pipe')}


def test_email_date_header_cannot_borrow_a_received_date(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Date: January 5, 2024\nReceived: January 6, 2024\n'
            'Subject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email Date header?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED',
              'answer':'Email Date header: January 5, 2024.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}
    wrong={**grounded,'answer':'Email Date header: January 6, 2024.'}

    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='email Date header'):
        validate_answer_model(wrong,evidence,question)


def test_email_date_header_accepts_formatting_only_changes(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Date: Tue, 5 Jan 2024 10:30:00 -0500\nSubject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='When was the email sent?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED',
            'answer':'Email Date header: “tue, 5  jan 2024 10:30:00 -0500”.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


def test_email_sent_date_question_accepts_an_explicit_sent_value(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Sent: January 5, 2024\nSubject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='When was the email sent?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED','answer':'The email was sent January 5, 2024.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


def test_email_sent_date_question_rejects_an_evasive_answer(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Date: January 5, 2024\nReceived: January 6, 2024\n'
            'Subject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='When was the email sent?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evasive={'status':'ANSWERED','answer':'The message has a January date.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires a Date header or Sent value'):
        validate_answer_model(evasive,evidence,question)


def test_email_date_header_cannot_be_recombined(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Date: Tue, 5 Jan 2024 10:30:00 -0500\n'
            'Date: Wed, 6 Jan 2024 11:45:00 -0500\nSubject: RFI thread')
    run=_source_evidence(client,project,[('thread.eml',[source])])
    question='What are the email Date headers?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED',
           'answer':'Email Date header: Tue, 5 Jan 2024 11:45:00 -0500.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='email Date header'):
        validate_answer_model(wrong,evidence,question)


def test_email_date_header_question_requires_an_explicit_header_value(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'Date: January 5, 2024\nSubject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the email date?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evasive={'status':'ANSWERED','answer':'The message is dated in January.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires an explicit header value'):
        validate_answer_model(evasive,evidence,question)


def test_email_source_finding_cannot_borrow_another_date_header(client, project):
    first=('From: Alice Architect <alice@example.test>\n'
           'Date: January 5, 2024\nSubject: Domestic Water Pipe')
    second=('From: Bob Builder <bob@example.test>\n'
            'Date: January 6, 2024\nSubject: Fire Alarm Coordination')
    run=_source_evidence(client,project,[
        ('water.eml',[first]),('fire-alarm.eml',[second])])
    question='Compare the email Date headers for Domestic Water Pipe and Fire Alarm Coordination.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The Email Date headers are different.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'water.eml',
             'statement':'Email Date header: January 6, 2024.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':first}]},
            {'source_type':'EMAIL','file_name':'fire-alarm.eml',
             'statement':'Email Date header: January 6, 2024.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':second}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains an email Date header'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','file_name'),[
    ('RFI 42\nDate: January 5, 2024\nQuestion: Can Type L copper be used?','RFI-42.txt'),
    ('Submittal 23-01\nDate: January 5, 2024\nStatus: PENDING','Submittal-23-01.txt'),
])
def test_workflow_form_date_does_not_ground_an_email_date_header(
        client, project, source, file_name):
    run=_source_evidence(client,project,[(file_name,[source])])
    question='What is the email Date header?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'Email Date header: January 5, 2024.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='email Date header'):
        validate_answer_model(wrong,evidence,question)


def test_email_date_header_parser_is_bounded_and_exact():
    text=('Date: Tue, 5 Jan 2024 10:30:00 -0500\n'
          'Received: Wed, 6 Jan 2024 11:45:00 -0500')

    assert _email_date_header_values(text)=={'tue, 5 jan 2024 10:30:00 -0500'}
    assert _email_date_header_claim_values(
        'Email Date header: “TUE, 5  JAN 2024 10:30:00 -0500”.')=={
            'tue, 5 jan 2024 10:30:00 -0500'}


def test_answer_rejects_recombined_email_address(client, project):
    source=('From: architect@example.test\nTo: owner@project.test\n'
            'Subject: RFI 42 response')
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Who sent the RFI 42 response?')
    wrong={'status':'ANSWERED',
           'answer':'The RFI 42 response came from architect@project.test.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='email address absent'):
        validate_answer_model(wrong,evidence,'Who sent the RFI 42 response?')


def test_answer_accepts_email_domain_case_formatting(client, project):
    source='From: architect@EXAMPLE.TEST\nSubject: RFI 42 response'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Who sent the RFI 42 response?')
    answer={'status':'ANSWERED',
            'answer':'The RFI 42 response came from architect@example.test.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    validate_answer_model(answer,evidence,'Who sent the RFI 42 response?')


def test_email_local_part_case_remains_exact(client, project):
    source='From: Architect@example.test\nSubject: RFI 42 response'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Who sent the RFI 42 response?')
    wrong={'status':'ANSWERED',
           'answer':'The RFI 42 response came from architect@example.test.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='email address absent'):
        validate_answer_model(wrong,evidence,'Who sent the RFI 42 response?')


def test_email_header_role_cannot_be_swapped(client, project):
    source='From: architect@example.test\nTo: owner@project.test'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,'Who is the email sender?')
    grounded={'status':'ANSWERED','answer':'The sender was architect@example.test.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-QA-1','quote':source}]}
    wrong={**grounded,'answer':'The sender was owner@project.test.'}

    validate_answer_model(grounded,evidence,'Who is the email sender?')
    with pytest.raises(ValueError,match='email address role'):
        validate_answer_model(wrong,evidence,'Who is the email sender?')


def test_email_participant_name_role_cannot_be_swapped(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'To: Bob Builder <bob@example.test>\nSubject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='Who is the email sender?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={'status':'ANSWERED','answer':'The sender was Alice Architect.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}
    wrong={**grounded,'answer':'The sender was Bob Builder.'}

    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='email participant role'):
        validate_answer_model(wrong,evidence,question)


def test_email_participant_name_accepts_formatting_only_changes(client, project):
    source=('From: "Alice Architect" <alice@example.test>\n'
            'Subject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='Who sent the email?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    answer={'status':'ANSWERED','answer':'Email sender: “alice   architect”.',
            'source_findings':[],
            'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(answer,evidence,question)


def test_email_participant_name_cannot_be_recombined(client, project):
    source=('From: Alice Architect <alice@example.test>\n'
            'To: Bob Builder <bob@example.test>\nSubject: RFI 42 response')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='Who sent the email?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'Email sender: Alice Builder.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='email participant role'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('question','header','claim'),[
    ('Who sent the email?','From: Alice Architect <alice@example.test>',
     'Email sender: Alice Architect.'),
    ('Who was copied on the email?','Cc: Carol Checker <carol@example.test>',
     'Cc: Carol Checker.'),
    ('Who was blind  copied on the email?','Bcc: Beth Bidder <beth@example.test>',
     'Bcc: Beth Bidder.'),
    ('Where should replies to the coordination email be sent?',
     'Reply-To: Erin Engineer <erin@example.test>','Reply-To: Erin Engineer.'),
])
def test_email_participant_question_requires_an_explicit_role_value(
        client, project, question, header, claim):
    source=header+'\nSubject: RFI 42 response'
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'What is the email subject?')
    evasive={'status':'ANSWERED','answer':'The email concerns RFI 42.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}
    grounded={**evasive,'answer':claim}

    expected_role=('REPLY_TO' if header.startswith('Reply-To:')
                   else header.split(':',1)[0].upper())
    assert questions_module._email_participant_intent_roles(question)=={expected_role}
    validate_answer_model(grounded,evidence,question)
    with pytest.raises(ValueError,match='requires an explicit role value'):
        validate_answer_model(evasive,evidence,question)


def test_email_source_finding_cannot_borrow_another_sender_name(client, project):
    first=('From: Alice Architect <alice@example.test>\n'
           'Subject: Domestic Water Pipe')
    second=('From: Bob Builder <bob@example.test>\n'
            'Subject: Fire Alarm Coordination')
    run=_source_evidence(client,project,[
        ('water.eml',[first]),('fire-alarm.eml',[second])])
    question='Compare the email senders for Domestic Water Pipe and Fire Alarm Coordination.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The emails have different senders.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'water.eml',
             'statement':'Email sender: Bob Builder.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':first}]},
            {'source_type':'EMAIL','file_name':'fire-alarm.eml',
             'statement':'Email sender: Bob Builder.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':second}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains an email participant role'):
        validate_answer_model(wrong,evidence,question)


def test_email_sender_comparison_requires_explicit_role_values(client, project):
    first=('From: Alice Architect <alice@example.test>\n'
           'Subject: Domestic Water Pipe')
    second=('From: Bob Builder <bob@example.test>\n'
            'Subject: Fire Alarm Coordination')
    run=_source_evidence(client,project,[
        ('water.eml',[first]),('fire-alarm.eml',[second])])
    question='Compare the email senders for Domestic Water Pipe and Fire Alarm Coordination.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    vague={
        'status':'ANSWERED','answer':'The messages involve different people.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'water.eml',
             'statement':'The first message concerns domestic water pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':first}]},
            {'source_type':'EMAIL','file_name':'fire-alarm.eml',
             'statement':'The second message concerns fire alarm coordination.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':second}]},
        ],
    }

    with pytest.raises(ValueError,match='requires an explicit role value'):
        validate_answer_model(vague,evidence,question)


@pytest.mark.parametrize(('source','file_name'),[
    ('RFI 42\nFrom: Alice Architect\nQuestion: Can Type L copper be used?','RFI-42.txt'),
    ('Submittal 23-01\nFrom: Alice Architect\nStatus: PENDING','Submittal-23-01.txt'),
])
def test_workflow_form_from_field_does_not_ground_an_email_sender_name(
        client, project, source, file_name):
    run=_source_evidence(client,project,[(file_name,[source])])
    question='Who is the email sender?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'Email sender: Alice Architect.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='email participant role'):
        validate_answer_model(wrong,evidence,question)


def test_email_participant_parser_preserves_header_roles_and_display_names():
    text=('From: "Architect, Alice" <alice@example.test>\n'
          'To: Bob Builder <bob@example.test>, Carol Checker <carol@example.test>\n'
          'Cc: Dave Designer <dave@example.test>\n'
          'Bcc: Beth Bidder <beth@example.test>\n'
          'Reply-To: Erin Engineer <erin@example.test>')

    assert _email_header_participant_role_values(text)=={
        ('FROM','architect, alice'),('TO','bob builder'),('TO','carol checker'),
        ('CC','dave designer'),('BCC','beth bidder'),('REPLY_TO','erin engineer')}
    assert _email_participant_claim_role_values(
        'According to: Alice Architect, the RFI remains open.')==set()


def test_email_address_stays_with_its_workflow(client, project):
    source=('RFI 42 response came from architect@example.test. '
            'Submittal 23-01 response came from vendor@project.test.')
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    evidence=retrieve_evidence(client.app.state.db,run,'Who sent the RFI 42 response?')
    wrong={'status':'ANSWERED',
           'answer':'RFI 42 response came from vendor@project.test.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow email address'):
        validate_answer_model(wrong,evidence,'Who sent the RFI 42 response?')


def test_submittal_finding_cannot_borrow_specification_email_address(
        client, project):
    spec='Specification contact: designer@example.test.'
    submittal='Submittal 23-01 contact: vendor@project.test.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 contacts.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources list different contacts.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':spec,
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 contact: designer@example.test.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='email address absent'):
        validate_answer_model(wrong,evidence,question)


def test_email_address_parser_is_bounded_and_preserves_header_roles():
    text=('From: Architect <architect@EXAMPLE.TEST>\n'
          'To: owner@project.test\nCc: reviewer@example.test')
    assert _email_address_values(text)=={
        'architect@example.test','owner@project.test','reviewer@example.test'}
    assert _email_address_role_values(text)=={
        ('FROM','architect@example.test'),('TO','owner@project.test'),
        ('CC','reviewer@example.test')}
    assert _email_address_values(
        'name@localhost bad..dots@example.test user@bad_domain.test')==set()


def test_source_finding_rejects_submittal_height_unit_as_width_unit(
        client, project):
    spec='Specification requires listed equipment dimensions.'
    submittal='Submittal 23-01 equipment width is 24 inch. Height is 24 mm.'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 equipment dimensions.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    next(item for item in evidence if item['file_name']=='submittal-23-01.txt')[
        'locator']['section']='Submittal 23-01 > REVIEW'
    wrong={
        'status':'ANSWERED','answer':'The sources describe equipment dimensions.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires listed equipment dimensions.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-01 equipment width is 24 mm.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='property unit'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_property_unit_cannot_be_recombined_across_rfis(client, project):
    source=('RFI 42 response: Pipe diameter is 2 inch. '
            'RFI 42 insulation thickness is 2 mm. '
            'RFI 43 response: Pipe diameter is 2 mm.')
    run=_evidence(client,project,[source])
    question='What pipe diameter does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 requires a 2 mm pipe diameter.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-QA-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow property unit'):
        validate_answer_model(wrong,evidence,question)


def test_email_answer_rejects_pressure_unit_as_strength_unit(client, project):
    source=('From: engineer@example.test\nSubject: RFI 42 response\n'
            'High pressure is 150 psi. Compressive strength is 150 MPa.')
    run=_source_evidence(client,project,[('rfi-42-response.eml',[source])])
    question='What compressive strength does RFI 42 require?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='Email > Current Body'
    wrong={'status':'ANSWERED','answer':'RFI 42 requires 150 psi compressive strength.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='property unit'):
        validate_answer_model(wrong,evidence,question)


def test_prefixed_rfi_identifiers_remain_exact_in_answer_citations(client, project):
    source='RFI ARC-0042 response: Use Type L copper. Reference ARC-42 remains open.'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What does RFI ARC-0042 require?')
    wrong={
        'status':'ANSWERED','answer':'RFI ARC-42 requires Type L copper.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='workflow identifier'):
        validate_answer_model(wrong,evidence,'What does RFI ARC-0042 require?')


def test_source_finding_rejects_a_different_submittal_identifier(
        client, project):
    spec='Specification requires Type L copper.'
    submittal=('Submittal 23-01 requires Type L copper. '
               'Coordination tag 23-02 remains open.')
    run=_source_evidence(client,project,[
        ('project-spec.txt',[spec]),('submittal-23-01.txt',[submittal])])
    question='Compare the specification and Submittal 23-01 pipe requirements.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':'The sources both require Type L copper.',
        'citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires Type L copper.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':spec}]},
            {'source_type':'SUBMITTAL','file_name':'submittal-23-01.txt',
             'statement':'Submittal 23-02 requires Type L copper.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':submittal}]},
        ],
    }

    with pytest.raises(ValueError,match='workflow identifier'):
        validate_answer_model(wrong,evidence,question)


def test_comparison_accepts_a_csi_section_in_a_generic_file_as_specification(client, project):
    run = _source_evidence(client, project, [
        ('combined-project.pdf', ['Domestic water pipe shall be 2 inch Type L copper.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id LIMIT 1',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='Section 22 11 16'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    evidence=retrieve_evidence(
        db,run,'Compare the specification and RFI 42 pipe requirements.')
    comparison={
        'status':'ANSWERED','answer':'RFI 42 increases the pipe size.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'combined-project.pdf',
             'statement':'The specification requires 2 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Domestic water pipe shall be 2 inch Type L copper.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 requires 3 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    validate_answer_model(
        comparison,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_question_api_rejects_cross_project_run(client, project):
    run = _evidence(client, project, ['Use Type L copper.'])
    other = client.post('/api/projects', json={'name': 'Other project'}).json()

    response = client.post(f'/api/projects/{other["id"]}/questions', json={
        'run_id': run['id'], 'question': 'What pipe material is required?'
    })

    assert response.status_code == 404
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


def test_paid_question_is_blocked_before_http_while_an_analysis_is_active(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    active = client.app.state.runner.create(project['id'])
    assert active['status'] == 'QUEUED'
    requests = []
    gateway = Gateway(_live_settings(tmp_path), client.app.state.db,
                      httpx.Client(transport=httpx.MockTransport(
                          lambda request: (requests.append(request), httpx.Response(500))[1])))

    with pytest.raises(BudgetError, match='active analysis'):
        ProjectQuestions(client.app.state.db, gateway).ask(
            run, 'What is the water service pipe material and size?')

    assert requests == []
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


@pytest.mark.parametrize(('kind','identifier','role','statuses'),[
    ('RFI','42','RESPONSE',('OPEN','CLOSED')),
    ('SUBMITTAL','23-01','SUBMITTAL',('PENDING','REJECTED')),
    ('SUBMITTAL','23-02','SUBMITTAL',('APPROVED AS NOTED','APPROVED')),
])
def test_full_workflow_index_blocks_a_status_conflict_hidden_from_retrieval(
        client, project, tmp_path, kind, identifier, role, statuses):
    question=f'What is the status of {kind.title()} {identifier}?'
    run=_evidence(client,project,[f'{kind} {identifier} status: {statuses[0]}.'])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([
        {'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':kind,'identifier':identifier,'role':role,'status':statuses[0]}]}},
        {'document_id':'D-ARCHIVE','name':'archive.eml','classification_source':'MANUAL','summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':kind,'identifier':identifier,'role':role,'status':statuses[1]}]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))

    result=ProjectQuestions(client.app.state.db,gateway).ask(run,question,workflow_index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert f'{kind} {identifier}' in result['answer'] and result['retrieved_count']==0
    assert all(status in result['answer'] for status in statuses)
    assert current['name'] in result['answer'] and 'archive.eml' in result['answer']
    assert '[detected]' in result['answer'] and '[manual correction]' in result['answer']
    conflict=result['workflow_conflicts'][0]
    assert conflict['workflow_type']==kind and conflict['identifier']==identifier
    assert {item['status'] for item in conflict['statuses']}==set(statuses)
    assert {source['file_name'] for item in conflict['statuses']
            for source in item['sources']}=={current['name'],'archive.eml'}
    assert {source['classification_source'] for item in conflict['statuses']
            for source in item['sources']}=={'DETECTED','MANUAL'}
    detected=next(source for item in conflict['statuses'] for source in item['sources']
                  if source['classification_source']=='DETECTED')
    assert detected['citation']['quote']==f'{kind} {identifier} status: {statuses[0]}.'
    assert all('citation' not in source for item in conflict['statuses']
               for source in item['sources'] if source['classification_source']=='MANUAL')
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_status_uses_complete_index_without_retrieval_or_provider(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('current.eml',['From: architect@example.test\nSubject: RFI 42\nRFI 42 status: OPEN.']),
    ])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([
        {'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'}]}},
        {'document_id':'MANUAL','name':'reviewer-correction.eml',
         'classification_source':'MANUAL','summary':{
             'document_type':'EMAIL','workflow_contexts':[{
                 'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'}]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact status answer read evidence'))

    result=ProjectQuestions(client.app.state.db,gateway).ask(
        run,'What is the status of RFI 42?',workflow_index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='WORKFLOW_INDEX'
    assert result['retrieved_count']==0 and result['citations']==[]
    assert 'RFI 42' in result['answer'] and 'OPEN' in result['answer']
    status=result['workflow_statuses'][0]
    assert status['workflow_type']=='RFI' and status['identifier']=='42'
    assert status['statuses'][0]['status']=='OPEN'
    sources={source['file_name']:source for source in status['statuses'][0]['sources']}
    assert sources['current.eml']['citation']['quote']=='RFI 42 status: OPEN.'
    assert sources['reviewer-correction.eml']['classification_source']=='MANUAL'
    assert 'citation' not in sources['reviewer-correction.eml']
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_status_rejects_one_malformed_multi_state_without_provider(
        client, project, tmp_path, monkeypatch):
    run=_evidence(client,project,['RFI 42 status: OPEN CLOSED.'])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([{
        'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE',
                'status':'OPEN CLOSED'}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('ambiguous status read evidence'))

    result=ProjectQuestions(client.app.state.db,gateway).ask(
        run,'What is the status of RFI 42?',workflow_index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['retrieved_count']==0 and result['workflow_statuses']==[]
    assert result['workflow_conflicts'][0]['statuses'][0]['status']=='OPEN CLOSED'
    assert requests==[]


@pytest.mark.parametrize('status',[None,'APPROVED'])
def test_exact_workflow_status_without_a_supported_explicit_state_uses_evidence_path(
        client, project, tmp_path, monkeypatch, status):
    run=_evidence(client,project,['RFI 42 question.'])
    index=build_workflow_index([{'document_id':'RFI-42','name':'rfi-42.txt','summary':{
        'document_type':'RFI_QUESTION','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':status}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda _request:pytest.fail('empty evidence called provider'))))

    result=ProjectQuestions(client.app.state.db,gateway).ask(
        run,'What is the status of RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['answer_basis']=='NO_MATCHING_EVIDENCE' and result['workflow_statuses']==[]


@pytest.mark.parametrize(('question','expected'),[
    ('What is the status of RFI 42?',('RFI','42')),
    ('Tell me the disposition for Request for Information No. 0042.',('RFI','42')),
    ('Show me the status of Submittal 23-01.',('SUBMITTAL','23-01')),
    ('What is the status of Submission 23-01?',('SUBMITTAL','23-01')),
    ('What status does Submittal 23 05 00 - 01 have?',('SUBMITTAL','23 05 00-01')),
    ('Which disposition is RFI ARC-42?',('RFI','ARC-42')),
    ('Compare the status of RFI 42 and Submittal 23-01.',None),
    ('Why is RFI 42 open?',None),
    ('Is RFI 42 open?',None),
    ('What does RFI 42 require?',None),
    ('What is the status of RFI 42 and RFI 43?',None),
    ('What is the status of this email?',None),
])
def test_exact_workflow_status_question_boundary(question,expected):
    assert requested_workflow_status_item(question)==expected


@pytest.mark.parametrize(('question','expected'),[
    ('What is the subject of RFI 42?',('RFI','42')),
    ('Show me the Subject line for Request for Information No. 0042.',('RFI','42')),
    ('What subject does Submittal 23-01 have?',('SUBMITTAL','23-01')),
    ("What is Submission 23-01's subject line?",('SUBMITTAL','23-01')),
    ('Compare the subjects of RFI 42 and RFI 43.',None),
    ('What is the email subject for RFI 42?',None),
    ('What is the subject of this RFI?',None),
])
def test_exact_workflow_subject_question_boundary(question,expected):
    assert requested_workflow_subject_item(question)==expected
    assert requires_workflow_subject_index(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('What is the question in RFI 42?',('42','QUESTION')),
    ('What does Request for Information No. 0042 ask?',('42','QUESTION')),
    ('What question did RFI ARC-42 ask?',('ARC-42','QUESTION')),
    ('What is the official response to RFI 42?',('42','RESPONSE')),
    ('Show me the answer for Request for Information 0042.',('42','RESPONSE')),
    ('How was RFI ARC-42 answered?',('ARC-42','RESPONSE')),
    ('What does RFI 42 require?',None),
    ('Compare the responses to RFI 42 and RFI 43.',None),
    ('What is the response to Submittal 23-01?',None),
    ('What is the response to this RFI?',None),
])
def test_exact_rfi_content_question_boundary(question,expected):
    assert requested_rfi_content(question)==expected
    assert requires_rfi_content_index(question) is bool(expected)


def test_exact_rfi_question_and_response_use_parser_scopes_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42.pdf',[
        'Question:\nMay Type L copper be used?',
        'Official Response:\nProvide Type L copper pipe.',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=? ORDER BY id',(run['id'],))
    for row,section in zip(rows,('RFI 42 > QUESTION','RFI 42 > RESPONSE')):
        payload=json.loads(row['payload']);payload['locator']['section']=section
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None},
            {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
        ]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact RFI content used retrieval'))

    question=ProjectQuestions(db,gateway).ask(run,'What is the question in RFI 42?',index)
    response=ProjectQuestions(db,gateway).ask(run,'What is the official response to RFI 42?',index)

    assert question['status']=='ANSWERED' and question['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'May Type L copper be used?' in question['answer']
    assert [item['quote'] for item in question['citations']]==[
        'Question:\nMay Type L copper be used?']
    assert response['status']=='ANSWERED'
    assert 'Provide Type L copper pipe.' in response['answer']
    assert [item['quote'] for item in response['citations']]==[
        'Official Response:\nProvide Type L copper pipe.']
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_rfi_content_blocks_multiple_same_role_sources_before_evidence_read(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'first.pdf','role':'RESPONSE','source':'PRIMARY'},
        {'document_id':'D-2','file_name':'second.pdf','role':'RESPONSE','source':'PRIMARY'},
    ]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('ambiguous RFI content read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('ambiguous RFI content used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-RFI','status':'COMPLETED'},'What is the response to RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert '2 primary sources share that explicit role' in result['answer']
    assert result['citations']==[]


def test_exact_rfi_content_passage_cap_fails_closed_before_parsing(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'response.pdf','role':'RESPONSE','source':'PRIMARY'},
    ]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('RFI passage cap used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-RFI-CAP','status':'COMPLETED'},'What is the response to RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 source passages' in result['answer']
    assert result['citations']==[]


def test_exact_rfi_content_character_cap_fails_closed(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[
        f'Response part {number}: '+('x'*1580) for number in range(12)
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],
        'name':'RFI-42-response.pdf','summary':{'document_type':'RFI_RESPONSE',
        'workflow_contexts':[{'workflow_type':'RFI','identifier':'42','role':'RESPONSE',
                              'status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('RFI character cap used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the response to RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'exceeds 18,000 characters' in result['answer']
    assert result['citations']==[]


def test_exact_rfi_content_excludes_quoted_email_history(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('reply.eml',[
        'Response:\nUse an obsolete product.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='EMAIL > QUOTED HISTORY > RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'reply.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('empty quoted-history evidence called provider'))))

    result=ProjectQuestions(db,gateway).ask(
        run,'What is the response to RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


@pytest.mark.parametrize(('question','expected'),[
    ('What is the Spec Section for RFI 42?','42'),
    ('What specification section does Request for Information No. 0042 reference?','42'),
    ('Show me the specification section for RFI ARC-42.','ARC-42'),
    ('What spec section does RFI 42-1 use?','42-1'),
    ('What is the Spec Section for Submittal 23-01?',None),
    ('What is the description of RFI 42?',None),
    ('Compare the Spec Sections for RFI 42 and RFI 43.',None),
    ('What is the Spec Section for this RFI?',None),
])
def test_exact_rfi_spec_section_question_boundary(question,expected):
    assert requested_rfi_spec_section(question)==expected
    assert requires_rfi_spec_section_index(question) is bool(expected)


def test_exact_rfi_spec_section_merges_matching_labelled_primary_evidence(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Spec Section: 22 11 16 - Domestic Water Piping']),
        ('RFI-42-response.pdf',['Specification Section: 22  11 16 - Domestic  Water Piping']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',
                (run['id'],))
    summaries=[]
    for row in rows:
        payload=json.loads(row['payload'])
        role='QUESTION' if 'question' in row['name'].casefold() else 'RESPONSE'
        payload['locator']['section']=f'RFI 42 > {role}'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        summaries.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':f'RFI_{role}','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':role,'status':None}]}})
    index=build_workflow_index(summaries)
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact RFI Spec Section used retrieval'))

    result=ProjectQuestions(db,gateway).ask(
        run,'What is the Spec Section for RFI 42?',index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert '22 11 16 - Domestic Water Piping' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Spec Section: 22 11 16 - Domestic Water Piping',
        'Specification Section: 22  11 16 - Domestic  Water Piping'}
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_rfi_spec_section_blocks_conflicting_primary_values(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Spec Section: 22 11 16']),
        ('RFI-42-response.pdf',['Spec Section: 23 05 00']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',
                (run['id'],))
    summaries=[]
    for row in rows:
        payload=json.loads(row['payload'])
        role='QUESTION' if 'question' in row['name'].casefold() else 'RESPONSE'
        payload['locator']['section']=f'RFI 42 > {role}'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        summaries.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':f'RFI_{role}','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':role,'status':None}]}})
    index=build_workflow_index(summaries)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('conflicting RFI section used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the Spec Section for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Spec Section: 22 11 16','Spec Section: 23 05 00'}


@pytest.mark.parametrize('section',[
    'EMAIL > QUOTED HISTORY > RFI 42',
    'RFI 43',
])
def test_exact_rfi_spec_section_does_not_borrow_wrong_or_quoted_scope(
        client, project, monkeypatch, section):
    run=_source_evidence(client,project,[('reply.eml',['Spec Section: 22 11 16'])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'reply.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the Spec Section for RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_rfi_spec_section_caps_primary_sources_before_evidence_read():
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{value}','file_name':f'{value}.pdf','source':'PRIMARY'}
        for value in range(33)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large RFI section question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-LARGE-RFI','status':'COMPLETED'},
        'What is the Spec Section for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in result['answer']
    assert result['citations']==[]


def test_exact_rfi_spec_section_caps_candidate_passages_before_parsing():
    class LargeDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'rfi.pdf','source':'PRIMARY'}]}]}

    result=ProjectQuestions(LargeDatabase(),object()).ask(
        {'id':'RUN-LARGE-RFI-EVIDENCE','status':'COMPLETED'},
        'What is the Spec Section for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in result['answer']
    assert result['citations']==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which drawings does RFI 42 reference?',('RFI','42','DRAWING')),
    ('What sheets are mentioned in Submission 23-01?',
     ('SUBMITTAL','23-01','DRAWING')),
    ('List the details referenced by Request for Information No. 0042.',
     ('RFI','42','DETAIL')),
    ('What drawing references does Submittal MEP-023 list?',
     ('SUBMITTAL','MEP-023','ALL')),
    ('Which sheets and details does RFI ARC-42 reference?',('RFI','ARC-42','ALL')),
    ('Show me the drawings for RFI 42-1.',('RFI','42-1','DRAWING')),
    ('Which sheet applies to RFI 42?',None),
    ('Compare the drawings referenced by RFI 42 and RFI 43.',None),
    ('Which drawings does this RFI reference?',None),
])
def test_exact_workflow_drawing_reference_question_boundary(question,expected):
    assert requested_workflow_drawing_references(question)==expected
    assert requires_workflow_drawing_reference_index(question) is bool(expected)


def test_exact_workflow_drawing_references_merge_primary_sources_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['RFI 42: Coordinate Sheet A1.01 with Detail 3/A5.1.']),
        ('RFI-42-response.pdf',['Drawing A1.01 remains the referenced plan.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',
                (run['id'],))
    summaries=[]
    for row in rows:
        payload=json.loads(row['payload'])
        role='QUESTION' if 'question' in row['name'].casefold() else 'RESPONSE'
        payload['locator']['section']=f'RFI 42 > {role}'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        summaries.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':f'RFI_{role}','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':role,'status':None}]}})
    index=build_workflow_index(summaries)
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('drawing references used retrieval'))

    result=ProjectQuestions(db,gateway).ask(
        run,'What drawing references does RFI 42 list?',index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'Sheet A1.01' in result['answer'] and 'Detail 3/A5.1' in result['answer']
    assert result['answer'].count('A1.01')==1
    assert {item['quote'] for item in result['citations']}=={
        'RFI 42: Coordinate Sheet A1.01 with Detail 3/A5.1.',
        'Drawing A1.01 remains the referenced plan.'}
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_detail_request_excludes_sheet_references(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42.pdf',[
        'RFI 42: Coordinate Sheet A1.01 with Detail 3/A5.1.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('detail references used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'List the details referenced by RFI 42.',index)

    assert result['status']=='ANSWERED'
    assert 'Detail 3/A5.1' in result['answer'] and 'Sheet A1.01' not in result['answer']


def test_exact_submittal_drawing_reference_accepts_current_email_body(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('review.eml',[
        'Submittal 23-01 references Dwg. M-101 for coordination.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='EMAIL > BODY > SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'review.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
            'status':'PENDING'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email drawing reference used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which drawings does Submittal 23-01 reference?',index)

    assert result['status']=='ANSWERED' and 'Dwg. M-101' in result['answer']
    assert result['citations'][0]['quote']==(
        'Submittal 23-01 references Dwg. M-101 for coordination.')


@pytest.mark.parametrize('section',[
    'EMAIL > QUOTED HISTORY > RFI 42',
    'RFI 43',
])
def test_exact_workflow_drawing_reference_does_not_borrow_wrong_or_quoted_scope(
        client, project, monkeypatch, section):
    run=_source_evidence(client,project,[('reply.eml',['Use Sheet A1.01.'])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'reply.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(
        run,'Which drawings does RFI 42 reference?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_drawing_reference_rejects_multi_identity_statement(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('combined.pdf',[
        'RFI 42 and RFI 43 reference Sheet A1.01.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'combined.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(
        run,'Which drawings does RFI 42 reference?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_drawing_reference_caps_distinct_values(monkeypatch):
    text='RFI 42: '+', '.join(f'Sheet A1.{value:02d}' for value in range(1,10))+'.'
    evidence={'evidence_id':'EV-DRAWINGS','document_id':'D-1','file_sha256':'a'*64,
              'raw_text':text,'locator':{'section':'RFI 42'}}
    class DrawingDatabase:
        def all(self,*_args,**_kwargs):return [{'payload':json.dumps(evidence),
                                               'file_name':'rfi.pdf'}]
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'rfi.pdf','source':'PRIMARY'}]}]}
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('large drawing list used retrieval'))

    result=ProjectQuestions(DrawingDatabase(),object()).ask(
        {'id':'RUN-MANY-DRAWINGS','status':'COMPLETED'},
        'Which drawings does RFI 42 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 explicit requested drawing references' in result['answer']
    assert len(result['citations'])==1


def test_exact_workflow_drawing_reference_caps_primary_sources_before_evidence_read():
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':f'D-{value}','file_name':f'{value}.pdf','source':'PRIMARY'}
        for value in range(33)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large drawing question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-LARGE-DRAWING','status':'COMPLETED'},
        'Which drawings does Submittal 23-01 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in result['answer']
    assert result['citations']==[]


def test_exact_workflow_drawing_reference_caps_candidate_passages_before_parsing():
    class LargeDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'rfi.pdf','source':'PRIMARY'}]}]}

    result=ProjectQuestions(LargeDatabase(),object()).ask(
        {'id':'RUN-LARGE-DRAWING-EVIDENCE','status':'COMPLETED'},
        'Which drawings does RFI 42 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in result['answer']
    assert result['citations']==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which paragraphs does RFI 42 reference?',('RFI','42','PARAGRAPH')),
    ('What clauses are mentioned in Submission 23-01?',('SUBMITTAL','23-01','CLAUSE')),
    ('List the articles referenced by Request for Information No. 0042.',
     ('RFI','42','ARTICLE')),
    ('Which paragraphs, clauses, and articles does Submittal MEP-023 reference?',
     ('SUBMITTAL','MEP-023','ALL')),
    ('Show me the paras for RFI 42-1.',('RFI','42-1','PARAGRAPH')),
    ('Which paragraph applies to RFI 42?',None),
    ('Compare the clauses referenced by RFI 42 and RFI 43.',None),
    ('Which articles does this Submittal reference?',None),
])
def test_exact_workflow_clause_reference_question_boundary(question,expected):
    assert requested_workflow_clause_references(question)==expected
    assert requires_workflow_clause_reference_index(question) is bool(expected)


def test_exact_workflow_clause_references_merge_typed_primary_sources_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',[
            'RFI 42: Follow Paragraph 2.3.1 and Clause 4.5.6.']),
        ('RFI-42-response.pdf',[
            'Para. 2.3.1 remains applicable; coordinate Article 1.2a.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',
                (run['id'],))
    summaries=[]
    for row in rows:
        payload=json.loads(row['payload'])
        role='QUESTION' if 'question' in row['name'].casefold() else 'RESPONSE'
        payload['locator']['section']=f'RFI 42 > {role}'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        summaries.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':f'RFI_{role}','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':role,'status':None}]}})
    index=build_workflow_index(summaries)
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('clause references used retrieval'))

    result=ProjectQuestions(db,gateway).ask(
        run,'Which paragraphs, clauses, and articles does RFI 42 reference?',index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'Paragraph 2.3.1' in result['answer']
    assert 'Clause 4.5.6' in result['answer'] and 'Article 1.2a' in result['answer']
    assert result['answer'].casefold().count('2.3.1')==1
    assert {item['quote'] for item in result['citations']}=={
        'RFI 42: Follow Paragraph 2.3.1 and Clause 4.5.6.',
        'Para. 2.3.1 remains applicable; coordinate Article 1.2a.'}
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_paragraph_request_keeps_reference_types_distinct(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42.pdf',[
        'RFI 42: Follow Paragraph 2.3.1 and Article 2.3.1.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('paragraph references used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which paragraphs does RFI 42 reference?',index)

    assert result['status']=='ANSWERED'
    assert 'Paragraph 2.3.1' in result['answer'] and 'Article 2.3.1' not in result['answer']


def test_exact_submittal_clause_reference_accepts_current_email_body(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('review.eml',[
        'Submittal 23-01 references Clause 3.4.5 for coordination.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='EMAIL > BODY > SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'review.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
            'status':'PENDING'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email clause reference used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which clauses does Submittal 23-01 reference?',index)

    assert result['status']=='ANSWERED' and 'Clause 3.4.5' in result['answer']
    assert result['citations'][0]['quote']==(
        'Submittal 23-01 references Clause 3.4.5 for coordination.')


@pytest.mark.parametrize('section',[
    'EMAIL > QUOTED HISTORY > RFI 42',
    'RFI 43',
])
def test_exact_workflow_clause_reference_does_not_borrow_wrong_or_quoted_scope(
        client, project, monkeypatch, section):
    run=_source_evidence(client,project,[('reply.eml',['Follow Paragraph 2.3.1.'])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'reply.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(
        run,'Which paragraphs does RFI 42 reference?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_clause_reference_rejects_multi_identity_statement(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('combined.pdf',[
        'RFI 42 and RFI 43 reference Paragraph 2.3.1.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'combined.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(
        run,'Which paragraphs does RFI 42 reference?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_clause_reference_caps_distinct_values(monkeypatch):
    text='RFI 42: '+', '.join(f'Paragraph 2.3.{value}' for value in range(1,10))+'.'
    evidence={'evidence_id':'EV-CLAUSES','document_id':'D-1','file_sha256':'a'*64,
              'raw_text':text,'locator':{'section':'RFI 42'}}
    class ClauseDatabase:
        def all(self,*_args,**_kwargs):return [{'payload':json.dumps(evidence),
                                               'file_name':'rfi.pdf'}]
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'rfi.pdf','source':'PRIMARY'}]}]}
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('large clause list used retrieval'))

    result=ProjectQuestions(ClauseDatabase(),object()).ask(
        {'id':'RUN-MANY-CLAUSES','status':'COMPLETED'},
        'Which paragraphs does RFI 42 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 explicit requested paragraph, clause or article references' in result['answer']
    assert len(result['citations'])==1


def test_exact_workflow_clause_reference_caps_primary_sources_before_evidence_read():
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':f'D-{value}','file_name':f'{value}.pdf','source':'PRIMARY'}
        for value in range(33)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large clause question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-LARGE-CLAUSE','status':'COMPLETED'},
        'Which clauses does Submittal 23-01 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in result['answer']
    assert result['citations']==[]


def test_exact_workflow_clause_reference_caps_candidate_passages_before_parsing():
    class LargeDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','file_name':'rfi.pdf','source':'PRIMARY'}]}]}

    result=ProjectQuestions(LargeDatabase(),object()).ask(
        {'id':'RUN-LARGE-CLAUSE-EVIDENCE','status':'COMPLETED'},
        'Which clauses does RFI 42 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in result['answer']
    assert result['citations']==[]


@pytest.mark.parametrize(('question','expected'),[
    ('What is the Spec Section for Submittal 23-01?',('23-01','SPEC_SECTION')),
    ('What specification section does Submission 23 05 00 - 01 reference?',
     ('23 05 00-01','SPEC_SECTION')),
    ('What is the description of Submittal 23-01?',('23-01','DESCRIPTION')),
    ('What description does Submission MEP-023 have?',('MEP-023','DESCRIPTION')),
    ('What are the review comments for Submittal 23-01?',('23-01','REVIEW_COMMENTS')),
    ('Show me the reviewer comment on Submission 23-01.',('23-01','REVIEW_COMMENTS')),
    ('Tell me the comments for Submittal 23-01.',('23-01','REVIEW_COMMENTS')),
    ('What is the status of Submittal 23-01?',None),
    ('What is the description of RFI 42?',None),
    ('Compare the descriptions of Submittal 23-01 and Submittal 23-02.',None),
    ('What are the review comments for this Submittal?',None),
])
def test_exact_submittal_field_question_boundary(question,expected):
    assert requested_submittal_field(question)==expected
    assert requires_submittal_field_index(question) is bool(expected)


def test_exact_submittal_fields_use_labelled_primary_evidence_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[
        'Specification   Section: 23 05 00 - General-Duty Valves',
        'Description:\nPump P-1 product data',
        'Review Comments:\nRevise pump selection.\nCoordinate voltage.',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=? ORDER BY id',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],
        'name':'Submittal-23-01.pdf','summary':{'document_type':'SUBMITTAL',
        'workflow_contexts':[{'workflow_type':'SUBMITTAL','identifier':'23-01',
                              'role':'SUBMITTAL','status':'PENDING'}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact Submittal field used retrieval'))

    section=ProjectQuestions(db,gateway).ask(
        run,'What is the Spec Section for Submittal 23-01?',index)
    description=ProjectQuestions(db,gateway).ask(
        run,'What is the description of Submittal 23-01?',index)
    comments=ProjectQuestions(db,gateway).ask(
        run,'What are the review comments for Submittal 23-01?',index)

    assert section['status']=='ANSWERED' and section['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert '23 05 00 - General-Duty Valves' in section['answer']
    assert section['citations'][0]['quote']==(
        'Specification   Section: 23 05 00 - General-Duty Valves')
    assert description['status']=='ANSWERED' and 'Pump P-1 product data' in description['answer']
    assert description['citations'][0]['quote']=='Description:\nPump P-1 product data'
    assert comments['status']=='ANSWERED'
    assert 'Revise pump selection. Coordinate voltage.' in comments['answer']
    assert comments['citations'][0]['quote']==(
        'Review Comments:\nRevise pump selection.\nCoordinate voltage.')
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_submittal_field_blocks_conflicting_explicit_values(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[
        'Description: Pump P-1 product data',
        'Description: Pump P-2 product data',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],
        'name':'Submittal-23-01.pdf','summary':{'document_type':'SUBMITTAL',
        'workflow_contexts':[{'workflow_type':'SUBMITTAL','identifier':'23-01',
                              'role':'SUBMITTAL','status':None}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('conflicting Submittal field used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the description of Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Description: Pump P-1 product data','Description: Pump P-2 product data'}


def test_exact_submittal_field_blocks_multiple_primary_sources_before_evidence_read(monkeypatch):
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':'D-1','file_name':'first.pdf','source':'PRIMARY'},
        {'document_id':'D-2','file_name':'second.pdf','source':'PRIMARY'},
    ]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('ambiguous Submittal field read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('ambiguous Submittal field used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-SUBMITTAL','status':'COMPLETED'},
        'What is the description of Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert '2 primary sources share that identifier' in result['answer']
    assert result['citations']==[]


def test_exact_submittal_field_passage_cap_fails_closed(monkeypatch):
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':'D-1','file_name':'submittal.pdf','source':'PRIMARY'},
    ]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Submittal field cap used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-SUBMITTAL-CAP','status':'COMPLETED'},
        'What is the description of Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in result['answer']
    assert result['citations']==[]


@pytest.mark.parametrize(('section','text'),[
    ('EMAIL > QUOTED HISTORY > SUBMITTAL 23-01','Description: Obsolete pump data'),
    ('SUBMITTAL 23-01','Pump data for Spec Section 23 05 00'),
])
def test_exact_submittal_field_ignores_quoted_history_and_unlabelled_values(
        client, project, tmp_path, monkeypatch, section, text):
    run=_source_evidence(client,project,[('coordination.eml', [text])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':None}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('empty Submittal field evidence called provider'))))

    result=ProjectQuestions(db,gateway).ask(
        run,'What is the description of Submittal 23-01?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_submittal_field_accepts_current_email_body(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Comment: Revise pump selection.',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='EMAIL > BODY > SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':None}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('current Email field used retrieval'))

    result=ProjectQuestions(db,gateway).ask(
        run,'What are the review comments for Submittal 23-01?',index)

    assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']=='Comment: Revise pump selection.'
    assert requests==[]


def test_exact_workflow_subject_uses_primary_file_evidence_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-form.pdf',['RFI 42\nSubject: Domestic Water Pipe\nQuestion: May Type L be used?']),
    ])
    db=client.app.state.db
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    index=build_workflow_index([{'document_id':document['document_id'],
        'name':document['name'],'summary':{'document_type':'RFI_QUESTION','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact Subject used ordinary retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,'What is the subject of RFI 42?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'Domestic Water Pipe' in result['answer']
    assert result['citations'][0]['quote']=='Subject: Domestic Water Pipe'
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_subject_blocks_conflicting_primary_values_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['RFI 42\nSubject: Domestic Water Pipe']),
        ('RFI-42-response.pdf',['RFI 42\nSubject: Fire Alarm Coordination']),
    ])
    db=client.app.state.db
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    index=build_workflow_index([
        {'document_id':documents['RFI-42-question.pdf'],'name':'RFI-42-question.pdf',
         'summary':{'document_type':'RFI_QUESTION','workflow_contexts':[{
             'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}},
        {'document_id':documents['RFI-42-response.pdf'],'name':'RFI-42-response.pdf',
         'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
             'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('conflicting Subject used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,'What is the subject of RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Subject: Domestic Water Pipe','Subject: Fire Alarm Coordination'}
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_subject_primary_document_cap_fails_closed(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','document_type':'RFI_RESPONSE','source':'PRIMARY'}
        for number in range(33)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('document cap still read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('document cap used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-CAP','status':'COMPLETED'},'What is the subject of RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in result['answer']
    assert result['citations']==[]


def test_exact_workflow_subject_passage_cap_fails_closed(monkeypatch):
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':'D-1','document_type':'SUBMITTAL','source':'PRIMARY'}]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(513)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('passage cap used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-CAP','status':'COMPLETED'},
        'What is the subject of Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 512 candidate source passages' in result['answer']
    assert result['citations']==[]


def test_email_subject_cannot_answer_an_exact_rfi_form_subject(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('coordination.eml',['From: architect@example.test\nSubject: RFI 42 Domestic Water Pipe']),
    ])
    db=client.app.state.db
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    index=build_workflow_index([{'document_id':document['document_id'],
        'name':document['name'],'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',lambda *_args,**_kwargs:[])
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('missing form Subject called provider'))))

    result=ProjectQuestions(db,gateway).ask(run,'What is the subject of RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_an_exact_subject(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('Submittal-23-01.pdf',[
            'Submittal 23-01\nSubject: Domestic Water Pipe Product Data']),
    ])
    db=client.app.state.db
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],document['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact Subject route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the subject of Submittal 23-01?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Subject: Domestic Water Pipe Product Data'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_exact_rfi_response(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-response.pdf',['Official Response:\nProvide Type L copper pipe.']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'RFI_RESPONSE','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact RFI response route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the response to RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Official Response:\nProvide Type L copper pipe.'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_exact_rfi_spec_section(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Spec Section: 22 11 16 - Domestic Water Piping']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > QUESTION'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'RFI_QUESTION','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact RFI section route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the Spec Section for RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==(
        'Spec Section: 22 11 16 - Domestic Water Piping')
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_exact_drawing_references(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-response.pdf',['RFI 42 response: Coordinate Sheet A1.01.']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'RFI_RESPONSE','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('drawing route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which drawings does RFI 42 reference?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'Sheet A1.01' in result['answer']
    assert result['citations'][0]['quote']=='RFI 42 response: Coordinate Sheet A1.01.'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_exact_clause_references(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-response.pdf',['RFI 42 response: Follow Paragraph 2.3.1.']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'RFI_RESPONSE','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('clause route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which paragraphs does RFI 42 reference?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'Paragraph 2.3.1' in result['answer']
    assert result['citations'][0]['quote']=='RFI 42 response: Follow Paragraph 2.3.1.'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_exact_submittal_field(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('Submittal-23-01.pdf',['Spec Section: 23 05 00 - General-Duty Valves']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact Submittal field route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the Spec Section for Submittal 23-01?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Spec Section: 23 05 00 - General-Duty Valves'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('What is the due date for RFI 42?',('RFI','42','DUE')),
    ('What is the expected response date for RFI 42?',('RFI','42','DUE')),
    ('When was RFI 0042 issued?',('RFI','42','ISSUED')),
    ('What is the response date for RFI ARC-42?',('RFI','ARC-42','RESPONSE')),
    ('What is the submission date of Submittal 23-01?',
     ('SUBMITTAL','23-01','SUBMITTED')),
    ('What is the receipt date for Submittal 23-01?',
     ('SUBMITTAL','23-01','RECEIVED')),
    ('When was Submittal 23-01 approved?',('SUBMITTAL','23-01','APPROVED')),
    ('What is the revision date for Submission 23 05 00 - 01?',
     ('SUBMITTAL','23 05 00-01','REVISION')),
    ('What dates are recorded for RFI 42?',None),
    ('When was the email sent?',None),
    ('What is the due date for RFI 42 and RFI 43?',None),
])
def test_exact_workflow_date_question_boundary(question,expected):
    assert requested_workflow_date_item(question)==expected
    assert requires_workflow_date_index(question) is bool(expected)


@pytest.mark.parametrize(('file_name','document_type','context','section','question','quote'),[
    ('RFI-42-question.pdf','RFI_QUESTION',
     {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'},
     'RFI 42 > QUESTION','What is the due date for RFI 42?',
     'Due Date: January 5, 2024'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','What is the submission date of Submittal 23-01?',
     'Submission Date: 2024-02-06'),
    ('RFI-42-response.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','What is the response date for RFI 42?',
     'Response Date: 7 March 2024'),
])
def test_exact_workflow_dates_answer_locally_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context,
        section, question, quote):
    run=_source_evidence(client,project,[(file_name,[quote])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact workflow date used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==quote
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_date_blocks_conflicting_values_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42.pdf',[
        'Due Date: 2024-01-05','Response Due Date: January 6, 2024',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > QUESTION'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_QUESTION','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('conflicting workflow date used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,'What is the due date for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Due Date: 2024-01-05','Response Due Date: January 6, 2024'}
    assert requests==[]


def test_exact_workflow_date_accepts_equivalent_formats_across_primary_sources(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Due Date: 2024-01-05']),
        ('RFI-42-log.pdf',['Response Due Date: January 5, 2024']),
    ])
    db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > QUESTION'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    context={'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}
    index=build_workflow_index([{
        'document_id':document_id,'name':file_name,
        'summary':{'document_type':'RFI_QUESTION','workflow_contexts':[context]}}
        for file_name,document_id in documents.items()])
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('equivalent workflow dates called provider'))))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('equivalent workflow dates used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,'When is RFI 42 due?',index)

    assert result['status']=='ANSWERED'
    assert len(result['citations'])==2
    assert {item['quote'] for item in result['citations']}=={
        'Due Date: 2024-01-05','Response Due Date: January 5, 2024'}


def test_exact_workflow_date_does_not_borrow_another_identity(
        client, project, tmp_path, monkeypatch):
    text='RFI 42 Issued: 2024-01-05. RFI 43 Due Date: 2024-01-06.'
    run=_source_evidence(client,project,[('RFI-42.pdf',[text])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > QUESTION'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_QUESTION','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('unsupported workflow date called provider'))))

    result=ProjectQuestions(db,gateway).ask(run,'What is the due date for RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_date_ignores_quoted_email_history(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',['Due Date: 2024-01-05'])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='EMAIL > QUOTED HISTORY > RFI 42 > QUESTION'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda _request:pytest.fail('quoted history date called provider'))))

    result=ProjectQuestions(db,gateway).ask(run,'What is the due date for RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_date_caps_primary_files_and_candidate_passages(monkeypatch):
    many={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    one={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('primary-file cap still read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(513)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('workflow date cap used retrieval'))

    document_cap=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-DATE-DOC-CAP','status':'COMPLETED'},
        'What is the due date for RFI 42?',many)
    passage_cap=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-DATE-PASSAGE-CAP','status':'COMPLETED'},
        'What is the due date for RFI 42?',one)

    assert document_cap['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in document_cap['answer']
    assert passage_cap['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 512 candidate source passages' in passage_cap['answer']


def test_question_api_loads_workflow_index_for_exact_workflow_date(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('RFI-42.pdf',['Due Date: 2024-01-05'])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > QUESTION'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'RFI_QUESTION','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('exact workflow date route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the due date for RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Due Date: 2024-01-05'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('What is the email subject?','SUBJECT'),
    ("What is this email's subject?",'SUBJECT'),
    ('What is the subject line of this email?','SUBJECT'),
    ('Show me the e-mail subject line.','SUBJECT'),
    ('What subject does the email have?','SUBJECT'),
    ('What is the email Date header?','DATE'),
    ("What is this email's date?",'DATE'),
    ('What is the Date header of this email?','DATE'),
    ('When was the email sent?','DATE'),
    ('What date was this email sent?','DATE'),
    ('Who sent the email?','FROM'),
    ('Who is this email from?','FROM'),
    ('Who is the email sender?','FROM'),
    ('Who received the email?','TO'),
    ('Who are the email recipients?','TO'),
    ('Who was this email sent to?','TO'),
    ('What is the email Cc header?','CC'),
    ("Who was CC'd on the email?",'CC'),
    ('Who is carbon copied on this e-mail?','CC'),
    ('What is this email Bcc?','BCC'),
    ("Who was BCC'd on this email?",'BCC'),
    ('Who is blindly copied on the email?','BCC'),
    ('What is the email Reply-To header?','REPLY_TO'),
    ('What is the Reply-To header of this e-mail?','REPLY_TO'),
    ('Where should replies to this email be sent?','REPLY_TO'),
    ('What is the subject of RFI 42?',None),
    ('Compare the email subjects.',None),
])
def test_single_email_header_question_boundary(question,expected):
    assert requested_single_email_header(question)==expected
    assert requested_email_header(question)==((expected,None) if expected else None)
    assert requires_single_email_header_index(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('What is the subject of email file coordination.eml?',
     ('SUBJECT','coordination.eml')),
    ('Show me the subject line for "Coordination Reply.eml".',
     ('SUBJECT','Coordination Reply.eml')),
    ('What is the Date header of coordination.msg?',('DATE','coordination.msg')),
    ('When was email file coordination.eml sent?',('DATE','coordination.eml')),
    ('Who sent coordination.eml?',('FROM','coordination.eml')),
    ('Who is "Coordination Reply.eml" from?',('FROM','Coordination Reply.eml')),
    ('Who received coordination.eml?',('TO','coordination.eml')),
    ('Who was email file coordination.msg sent to?',('TO','coordination.msg')),
    ('What is the Cc header of coordination.eml?',('CC','coordination.eml')),
    ("Who was CC'd on \"Coordination Reply.eml\"?",('CC','Coordination Reply.eml')),
    ('What is the Bcc header for coordination.msg?',('BCC','coordination.msg')),
    ('Who is blindly copied on coordination.eml?',('BCC','coordination.eml')),
    ('What is the Reply-To header of coordination.eml?',('REPLY_TO','coordination.eml')),
    ('Where should replies to "Coordination Reply.eml" be sent?',
     ('REPLY_TO','Coordination Reply.eml')),
    ('What is the subject of ../secret.eml?',None),
    ('What is the subject of report.pdf?',None),
    ('What is the subject of email file *.eml?',None),
])
def test_named_email_header_question_boundary(question,expected):
    assert requested_email_header(question)==expected
    assert requires_email_header_index(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('Which RFIs and Submittals does coordination.eml reference?',
     (('RFI','SUBMITTAL'),'coordination.eml')),
    ('Which RFI is referenced by "Coordination Reply.eml"?',
     (('RFI',),'Coordination Reply.eml')),
    ('Which Submittals are mentioned in this email?',(('SUBMITTAL',),None)),
    ('What Requests for Information are linked to coordination.msg?',
     (('RFI',),'coordination.msg')),
    ('List the RFIs and Submittals referenced in coordination.eml.',
     (('RFI','SUBMITTAL'),'coordination.eml')),
    ('Which RFIs are referenced by ../secret.eml?',None),
    ('Which RFIs does report.pdf reference?',None),
    ('Which RFIs does *.eml reference?',None),
    ('What is the subject of coordination.eml?',None),
])
def test_email_workflow_relationship_question_boundary(question,expected):
    assert requested_email_workflow_relations(question)==expected
    assert requires_email_workflow_relation_index(question) is bool(expected)


def test_named_email_workflow_relationships_answer_from_current_text_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Subject: RFI 0042 Domestic Water Coordination',
        'Coordinate Submittal 23-01 before procurement.',
    ])])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?
                   ORDER BY e.rowid''',(run['id'],))
    for index,row in enumerate(rows):
        payload=json.loads(row['payload'])
        payload['locator']['section']='EMAIL > HEADERS' if index==0 else 'EMAIL > BODY'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],
        'name':rows[0]['name'],'summary':{'document_type':'EMAIL','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}],
            'workflow_references':[
                {'workflow_type':'RFI','identifier':'0042'},
                {'workflow_type':'SUBMITTAL','identifier':'23-01'}]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email relationship used retrieval'))

    result=ProjectQuestions(db,gateway).ask(
        run,'Which RFIs and Submittals does COORDINATION.eml reference?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'RFI 42 (primary Email context)' in result['answer']
    assert 'Submittal 23-01 (explicit reference)' in result['answer']
    assert [item['quote'] for item in result['citations']]==[
        'Subject: RFI 0042 Domestic Water Coordination',
        'Coordinate Submittal 23-01 before procurement.',
    ]
    assert result['retrieved_count']==2 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_email_workflow_relationship_blocks_multiple_unspecified_emails_before_evidence_read(
        monkeypatch):
    index=build_workflow_index([
        {'document_id':'D-1','name':'first.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'D-2','name':'second.eml','summary':{'document_type':'EMAIL'}},
    ])
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('ambiguous Email relationship read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email relationship used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-EMAILS','status':'COMPLETED'},
        'Which RFIs does this email reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 Email files' in result['answer']
    assert result['citations']==[]


def test_email_workflow_relationship_rejects_quoted_history_only_index_link(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'RFI 99 was discussed in the earlier message.',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > QUOTED HISTORY'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':row['name'],
        'summary':{'document_type':'EMAIL','workflow_references':[
            {'workflow_type':'RFI','identifier':'99'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('quoted relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which RFIs does coordination.eml reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'exact current header/body text was not available for: RFI 99' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_email_workflow_relationship(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Subject: RFI 42 Domestic Water Coordination',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL',
            'workflow_contexts':[{'workflow_type':'RFI','identifier':'42',
                                  'role':'QUESTION','status':None}]})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email relationship API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which RFI does coordination.eml reference?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert 'RFI 42 (primary Email context)' in result['answer']
    assert result['citations'][0]['quote']=='Subject: RFI 42 Domestic Water Coordination'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which emails reference RFI 42?',('RFI','42')),
    ('Which Email files mention Request for Information No. 0042?',('RFI','42')),
    ('What messages are linked to Submittal 23-01?',('SUBMITTAL','23-01')),
    ('List emails referencing RFI ARC-42.',('RFI','ARC-42')),
    ('Show me Email files associated with Submittal 23 05 00 - 01.',
     ('SUBMITTAL','23 05 00-01')),
    ('Which emails reference RFI 42 and RFI 43?',None),
    ('Which emails were sent for RFI 42?',None),
    ('Which RFIs does coordination.eml reference?',None),
    ('List all emails in this run.',None),
])
def test_workflow_email_relationship_question_boundary(question,expected):
    assert requested_workflow_email_relations(question)==expected
    assert requires_workflow_email_relation_index(question) is bool(expected)


def test_workflow_email_relationships_answer_from_each_current_email_without_model(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('question.eml',['Subject: RFI 42 Domestic Water Coordination']),
        ('coordination.eml',['Coordinate Request for Information No. 0042 before release.']),
        ('submittal.eml',['Coordinate Submittal 23-01 before procurement.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents={row['name']:row['document_id'] for row in rows}
    for row in rows:
        payload=json.loads(row['payload'])
        payload['locator']['section']=('EMAIL > HEADERS' if row['name']=='question.eml'
                                       else 'EMAIL > BODY')
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([
        {'document_id':documents['question.eml'],'name':'question.eml','summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}},
        {'document_id':documents['coordination.eml'],'name':'coordination.eml','summary':{
            'document_type':'EMAIL','workflow_references':[{
                'workflow_type':'RFI','identifier':'0042'}]}},
        {'document_id':documents['submittal.eml'],'name':'submittal.eml','summary':{
            'document_type':'EMAIL','workflow_references':[{
                'workflow_type':'SUBMITTAL','identifier':'23-01'}]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('reverse Email relationship used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,'Which emails reference RFI 42?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'coordination.eml (explicit reference)' in result['answer']
    assert 'question.eml (primary Email context)' in result['answer']
    assert [item['quote'] for item in result['citations']]==[
        'Coordinate Request for Information No. 0042 before release.',
        'Subject: RFI 42 Domestic Water Coordination',
    ]
    assert 'submittal.eml' not in result['answer']
    assert result['retrieved_count']==2 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_email_relationship_rejects_quoted_history_only_member(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('archive.eml',[
        'RFI 99 was discussed in the earlier message.',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > QUOTED HISTORY'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':row['name'],
        'summary':{'document_type':'EMAIL','workflow_references':[{
            'workflow_type':'RFI','identifier':'99'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('quoted reverse relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'Which emails reference RFI 99?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'exact current header/body text was not available in: archive.eml' in result['answer']
    assert result['citations']==[]


def test_workflow_email_relationship_caps_associated_email_files_before_evidence_read(
        monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{value}','file_name':f'{value}.eml','document_type':'EMAIL',
         'source':'REFERENCE'} for value in range(9)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large reverse relationship read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('large reverse relationship used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-MANY-EMAILS','status':'COMPLETED'},
        'Which emails reference RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 exact Email associations' in result['answer']
    assert result['citations']==[]


def test_workflow_email_relationship_caps_current_passages_per_email(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[{
        'document_id':'D-1','file_name':'coordination.eml','document_type':'EMAIL',
        'source':'REFERENCE'}]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('large reverse relationship used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-MANY-PASSAGES','status':'COMPLETED'},
        'Which emails reference RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 current header/body passages' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_reverse_email_relationship(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Coordinate RFI 42 before release.',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL',
            'workflow_references':[{'workflow_type':'RFI','identifier':'42'}]})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('reverse Email API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which emails reference RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert 'coordination.eml (explicit reference)' in result['answer']
    assert result['citations'][0]['quote']=='Coordinate RFI 42 before release.'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which Submittals does RFI 42 reference?',('RFI','42','SUBMITTAL')),
    ('What Submissions are related to Request for Information No. 0042?',
     ('RFI','42','SUBMITTAL')),
    ('Which RFIs are listed for Submittal 23-01?',('SUBMITTAL','23-01','RFI')),
    ('Show me the Requests for Information linked to Submission MEP-023.',
     ('SUBMITTAL','MEP-023','RFI')),
    ('Which RFIs does RFI 42 reference?',None),
    ('Which Submittals does RFI 42 and RFI 43 reference?',None),
    ('Which Submittals apply to RFI 42?',None),
    ('How are RFI 42 and Submittal 23-01 related?',None),
])
def test_cross_workflow_relationship_question_boundary(question,expected):
    assert requested_cross_workflow_relations(question)==expected
    assert requires_cross_workflow_relation_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source','answer'),[
    ('RFI-42.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','Which Submittals does RFI 42 reference?',
     'Related Submittals: 23-01, MEP-023',
     'RFI 42 Related Submittals: "23-01, MEP-023".'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','Which RFIs are listed for Submittal 23-01?',
     'RFI References: RFI 0042 and ARC-7',
     'Submittal 23-01 Related RFIs: "42, ARC-7".'),
])
def test_cross_workflow_relationship_answers_explicit_fields_without_model_or_retrieval(
        client, project, monkeypatch, file_name, document_type, context, section,
        question, source, answer):
    run=_source_evidence(client,project,[(file_name,[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('cross-workflow relation used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,question,index)

    assert result['status']=='ANSWERED' and result['answer']==answer
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source and result['retrieved_count']==1
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_cross_workflow_relationship_does_not_infer_general_cooccurrence(
        client, project, monkeypatch):
    source='RFI 42 discusses coordination near Submittal 23-01.'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}],
            'workflow_references':[{'workflow_type':'SUBMITTAL','identifier':'23-01'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('cooccurrence used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which Submittals does RFI 42 reference?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'General co-occurrence is not treated as a relationship' in result['answer']
    assert result['citations']==[]


def test_cross_workflow_relationship_supports_explicit_empty_and_blocks_conflict(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Related Submittal(s): N/A']),
        ('RFI-42-response.pdf',['Submittal Reference: 23-01']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',
                (run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('empty relation used retrieval'))

    empty=ProjectQuestions(db,object()).ask(
        run,'Which Submittals does RFI 42 reference?',build_workflow_index(contexts[:1]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'Which Submittals does RFI 42 reference?',build_workflow_index(contexts))

    assert empty['status']=='ANSWERED'
    assert empty['answer']=='RFI 42 Related Submittals: none listed.'
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'both an empty marker and workflow identifiers' in conflicted['answer']
    assert len(conflicted['citations'])==2


@pytest.mark.parametrize(('section','answered'),[
    ('EMAIL > BODY > RFI 42',True),
    ('EMAIL > HEADERS > RFI 42',False),
    ('EMAIL > QUOTED HISTORY > RFI 42',False),
    ('EMAIL > SIGNATURE > RFI 42',False),
])
def test_cross_workflow_relationship_email_scope_boundary(
        client, project, monkeypatch, section, answered):
    source='Related Submittal: 23-01'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email relation used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which Submittals does RFI 42 reference?',index)

    assert (result['status']=='ANSWERED') is answered
    assert bool(result['citations']) is answered


def test_cross_workflow_relationship_caps_identifiers_and_candidate_passages(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    primary_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    class IdentifierDatabase:
        def all(self,*_args,**_kwargs):
            payload={'evidence_id':'EV-1','raw_text':(
                'Related Submittals: '+', '.join(f'S-{number}' for number in range(1,10))),
                'document_id':'D-1','file_sha256':'0'*64,
                'locator':{'section':'RFI 42 > RESPONSE'}}
            return [{'payload':json.dumps(payload),'file_name':'RFI-42.pdf'}]
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('primary relationship cap read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('relationship cap used retrieval'))

    identifiers=ProjectQuestions(IdentifierDatabase(),object()).ask(
        {'id':'RUN-REL-CAP','status':'COMPLETED'},
        'Which Submittals does RFI 42 reference?',index)
    passages=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-REL-CAP','status':'COMPLETED'},
        'Which Submittals does RFI 42 reference?',index)
    primaries=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-REL-CAP','status':'COMPLETED'},
        'Which Submittals does RFI 42 reference?',primary_index)

    assert identifiers['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 explicit Related Submittals' in identifiers['answer']
    assert 'more than 32 candidate source passages' in passages['answer']
    assert 'more than 32 primary files' in primaries['answer']


def test_cross_workflow_relationship_parsers_and_answer_validator(client, project):
    source='Related Submittals: 23-01, MEP-023'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Which Submittals does RFI 42 reference?')
    assert len(evidence)==1
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    expected={(('RFI','42'),'SUBMITTAL','23-01'),
              (('RFI','42'),'SUBMITTAL','MEP-023')}
    assert _cross_workflow_relation_source_values(
        source,'RFI 42 > RESPONSE')==expected
    assert _cross_workflow_relation_claim_values(
        'RFI 42 Related Submittals: "23-01, MEP-023".')==expected
    valid={'status':'ANSWERED','answer':(
        'RFI 42 Related Submittals: "23-01, MEP-023".'),
        'citations':[{'evidence_id':evidence[0]['evidence_id'],'quote':source}],
        'source_findings':[]}
    validate_answer_model(valid,evidence,'Which Submittals does RFI 42 reference?')

    invented={**valid,'answer':'RFI 42 Related Submittals: "23-01, MEP-999".'}
    omitted={**valid,'answer':'RFI 42 has an explicit relationship field.'}
    with pytest.raises(ValueError,match='cross-workflow relationship absent'):
        validate_answer_model(invented,evidence,'Which Submittals does RFI 42 reference?')
    with pytest.raises(ValueError,match='requires an explicit scoped relationship'):
        validate_answer_model(omitted,evidence,'Which Submittals does RFI 42 reference?')


def test_cross_workflow_validator_blocks_conflict_and_cross_source_borrowing(client, project):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Related Submittals: None']),
        ('RFI-42-response.pdf',['Related Submittals: 23-01']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.payload,d.name FROM evidence e JOIN documents d
                   ON d.id=e.document_id WHERE e.run_id=? ORDER BY d.name''',(run['id'],))
    evidence=[]
    for row in rows:
        item=json.loads(row['payload']);item['file_name']=row['name']
        item['locator']['section']='RFI 42 > RESPONSE';evidence.append(item)
    by_file={item['file_name']:item for item in evidence}
    question='Which Submittals does RFI 42 reference?'
    conflicted={'status':'ANSWERED','answer':'RFI 42 Related Submittals: "23-01".',
        'citations':[{'evidence_id':by_file['RFI-42-response.pdf']['evidence_id'],
                      'quote':'Related Submittals: 23-01'}],
        'source_findings':[]}
    borrowed={'status':'ANSWERED','answer':'The source reports one relationship.',
        'citations':[],'source_findings':[{
            'source_type':'RFI','file_name':'RFI-42-question.pdf',
            'statement':'RFI 42 Related Submittals: "23-01".',
            'citations':[{'evidence_id':by_file['RFI-42-question.pdf']['evidence_id'],
                          'quote':'Related Submittals: None'}]}]}

    with pytest.raises(ValueError,match='conflicting empty and nonempty'):
        validate_answer_model(conflicted,evidence,question)
    with pytest.raises(ValueError,match='cross-workflow relationship absent'):
        validate_answer_model(borrowed,evidence,question)


def test_question_api_loads_workflow_index_for_cross_workflow_relationship(
        client, project, monkeypatch):
    source='Related Submittal: 23-01'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'RFI_RESPONSE',
            'workflow_contexts':[{'workflow_type':'RFI','identifier':'42',
                                  'role':'RESPONSE','status':'ANSWERED'}]})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('cross-workflow API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which Submittals does RFI 42 reference?'})

    assert response.status_code==200
    assert response.json()['answer']=='RFI 42 Related Submittals: "23-01".'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which project files mention RFI 42?',('RFI','42')),
    ('Which documents reference Request for Information No. 0042?',('RFI','42')),
    ('What source files are associated with Submittal 23-01?',('SUBMITTAL','23-01')),
    ('List the files linked to RFI ARC-42.',('RFI','ARC-42')),
    ('Show me documents that contain Submittal 23 05 00 - 01.',
     ('SUBMITTAL','23 05 00-01')),
    ('Which project files mention RFI 42 and RFI 43?',None),
    ('Which email files mention RFI 42?',None),
    ('What files contain the response to RFI 42?',None),
    ('List all project files.',None),
])
def test_workflow_document_relationship_question_boundary(question,expected):
    assert requested_workflow_document_relations(question)==expected
    assert requires_workflow_document_relation_index(question) is bool(expected)


def test_workflow_document_relationships_answer_across_file_types_without_model(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-0042.pdf',['RFI 0042 Question: Confirm the domestic water routing.']),
        ('project-spec.pdf',['Coordinate RFI 42 before releasing the piping package.']),
        ('coordination.eml',['Subject: Request for Information No. 0042 coordination']),
        ('other.pdf',['RFI 43 Question: Confirm the fire alarm routing.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents={row['name']:row['document_id'] for row in rows}
    for row in rows:
        if row['name']!='coordination.eml':continue
        payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([
        {'document_id':documents['RFI-0042.pdf'],'name':'RFI-0042.pdf','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'0042','role':'QUESTION','status':None}]}},
        {'document_id':documents['project-spec.pdf'],'name':'project-spec.pdf','summary':{
            'document_type':'SPECIFICATION','workflow_references':[{
                'workflow_type':'RFI','identifier':'42'}]}},
        {'document_id':documents['coordination.eml'],'name':'coordination.eml','summary':{
            'document_type':'EMAIL','workflow_references':[{
                'workflow_type':'RFI','identifier':'0042'}]}},
        {'document_id':documents['other.pdf'],'name':'other.pdf','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'43','role':'QUESTION','status':None}]}},
    ])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which project files mention RFI 42?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert 'RFI-0042.pdf (RFI question; primary workflow source)' in result['answer']
    assert 'coordination.eml (Email; explicit reference)' in result['answer']
    assert 'project-spec.pdf (Specification; explicit reference)' in result['answer']
    assert 'other.pdf' not in result['answer']
    assert [item['quote'] for item in result['citations']]==[
        'RFI 0042 Question: Confirm the domestic water routing.',
        'Subject: Request for Information No. 0042 coordination',
        'Coordinate RFI 42 before releasing the piping package.',
    ]
    assert result['retrieved_count']==3
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_document_relationships_answer_submittal_sources_without_model(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('Submittal-23-01.pdf',['Submittal 23-01 Description: Domestic water piping.']),
        ('project-spec.pdf',['Review Submission 23-01 before procurement.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.document_id,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents={row['name']:row['document_id'] for row in rows}
    index=build_workflow_index([
        {'document_id':documents['Submittal-23-01.pdf'],'name':'Submittal-23-01.pdf',
         'summary':{'document_type':'SUBMITTAL','workflow_contexts':[{
             'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
             'status':None}]}},
        {'document_id':documents['project-spec.pdf'],'name':'project-spec.pdf','summary':{
            'document_type':'SPECIFICATION','workflow_references':[{
                'workflow_type':'SUBMITTAL','identifier':'23-01'}]}},
    ])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What source files are associated with Submittal 23-01?',index)

    assert result['status']=='ANSWERED'
    assert 'Submittal-23-01.pdf (Submittal; primary workflow source)' in result['answer']
    assert 'project-spec.pdf (Specification; explicit reference)' in result['answer']
    assert [item['quote'] for item in result['citations']]==[
        'Submittal 23-01 Description: Domestic water piping.',
        'Review Submission 23-01 before procurement.',
    ]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_document_relationship_requires_exact_text_in_every_indexed_file(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42.pdf',['RFI 42 Question: Confirm the routing.']),
        ('archive.eml',['RFI 42 appeared only in quoted history.']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents={row['name']:row['document_id'] for row in rows}
    for row in rows:
        if row['name']!='archive.eml':continue
        payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > QUOTED HISTORY'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([
        {'document_id':documents['RFI-42.pdf'],'name':'RFI-42.pdf','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}},
        {'document_id':documents['archive.eml'],'name':'archive.eml','summary':{
            'document_type':'EMAIL','workflow_references':[{
                'workflow_type':'RFI','identifier':'42'}]}},
    ])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which documents reference RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'exact source text was not available in: archive.eml' in result['answer']
    assert [item['quote'] for item in result['citations']]==[
        'RFI 42 Question: Confirm the routing.']


def test_workflow_document_relationship_rejects_vision_narration(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('drawing.pdf',[
        'The image appears to mention Submittal 23-01.',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['content_basis']='MODEL_VISION_OUTPUT'
    payload['extraction_method']='VISION'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':row['name'],
        'summary':{'document_type':'DRAWING','workflow_references':[{
            'workflow_type':'SUBMITTAL','identifier':'23-01'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'Which project files mention Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'exact source text was not available in: drawing.pdf' in result['answer']
    assert result['citations']==[]


def test_workflow_document_relationship_caps_files_before_evidence_read(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{value}','file_name':f'{value}.pdf',
         'document_type':'SPECIFICATION','source':'REFERENCE'} for value in range(9)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large file relationship read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-MANY-FILES','status':'COMPLETED'},
        'Which project files mention RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 exact project-file associations' in result['answer']
    assert result['citations']==[]


def test_workflow_document_relationship_rejects_duplicate_file_names_before_read(
        monkeypatch):
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':'D-1','file_name':'review.pdf','document_type':'SUBMITTAL',
         'source':'PRIMARY'},
        {'document_id':'D-2','file_name':'REVIEW.PDF','document_type':'SPECIFICATION',
         'source':'REFERENCE'},
    ]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('duplicate relationship read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-DUPLICATE-FILES','status':'COMPLETED'},
        'What source files are associated with Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'share the same file name' in result['answer']
    assert result['citations']==[]


def test_workflow_document_relationship_caps_candidate_passages(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[{
        'document_id':'D-1','file_name':'project-spec.pdf',
        'document_type':'SPECIFICATION','source':'REFERENCE'}]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-MANY-PASSAGES','status':'COMPLETED'},
        'Which project files mention RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate passages' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_project_file_relationship(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('project-spec.pdf',[
        'Coordinate RFI 42 before releasing the piping package.',
    ])])
    db=client.app.state.db
    row=db.one('''SELECT e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({
            'document_type':'SPECIFICATION','workflow_references':[{
                'workflow_type':'RFI','identifier':'42'}]})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('file relationship API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Which project files mention RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert 'project-spec.pdf (Specification; explicit reference)' in result['answer']
    assert result['citations'][0]['quote']==(
        'Coordinate RFI 42 before releasing the piping package.')
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which emails are in the same thread as reply.eml?','reply.eml'),
    ('What messages are in reply.msg\'s thread?','reply.msg'),
    ('List the emails in the same thread as "Coordination Reply.eml".',
     'Coordination Reply.eml'),
    ('Show me the email thread containing reply.eml.','reply.eml'),
    ('What is the thread for email file reply.msg?','reply.msg'),
    ('Which emails are in the same thread as ../reply.eml?',None),
    ('Which emails are in the same thread as *.eml?',None),
    ('Which emails are in the same thread as reply.pdf?',None),
    ('Which emails reference RFI 42?',None),
])
def test_email_thread_question_boundary(question,expected):
    assert requested_email_thread(question)==expected
    assert requires_email_thread_index(question) is bool(expected)


def test_email_thread_question_returns_header_order_without_model_or_evidence_read(
        monkeypatch):
    parent='MSG-'+'a'*24;reply='MSG-'+'b'*24;external='MSG-'+'c'*24
    index=build_workflow_index([
        {'document_id':'D-PARENT','name':'z-parent.eml','summary':{
            'document_type':'EMAIL','email_thread':{
                'message_key':parent,'parent_message_key':None,'reference_keys':[]}}},
        {'document_id':'D-REPLY','name':'a-reply.eml','summary':{
            'document_type':'EMAIL','email_thread':{
                'message_key':reply,'parent_message_key':parent,
                'reference_keys':[parent,external]}}},
    ])
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('Email thread question read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-THREAD','status':'COMPLETED'},
        'Which emails are in the same thread as a-reply.eml?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='WORKFLOW_INDEX'
    assert '2 messages in header-derived order: z-parent.eml; a-reply.eml' in result['answer']
    assert '1 hashed header reference to messages not present in this run' in result['answer']
    assert result['citations']==[] and result['retrieved_count']==0


def test_email_thread_question_answers_a_standalone_indexed_message(monkeypatch):
    index=build_workflow_index([{'document_id':'D-ONLY','name':'only.eml','summary':{
        'document_type':'EMAIL','email_thread':{
            'message_key':None,'parent_message_key':None,'reference_keys':[]}}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-SINGLE-THREAD','status':'COMPLETED'},
        'Show me the email thread containing only.eml.',index)

    assert result['status']=='ANSWERED'
    assert 'only analyzed message in its indexed Email thread' in result['answer']
    assert result['citations']==[]


def test_email_thread_question_fails_closed_on_a_header_cycle(monkeypatch):
    first='MSG-'+'a'*24;second='MSG-'+'b'*24
    index=build_workflow_index([
        {'document_id':'D-A','name':'a.eml','summary':{'document_type':'EMAIL',
         'email_thread':{'message_key':first,'parent_message_key':second,
                         'reference_keys':[]}}},
        {'document_id':'D-B','name':'b.eml','summary':{'document_type':'EMAIL',
         'email_thread':{'message_key':second,'parent_message_key':first,
                         'reference_keys':[]}}},
    ])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-CYCLE','status':'COMPLETED'},
        'Which messages are in a.eml\'s thread?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'is ambiguous' in result['answer']
    assert 'Retained files for review: a.eml; b.eml' in result['answer']
    assert 'form a cycle' in result['answer']
    assert result['citations']==[]


def test_email_thread_question_rejects_duplicate_exact_file_names_before_read(
        monkeypatch):
    index=build_workflow_index([
        {'document_id':'D-1','name':'same.eml','summary':{'document_type':'EMAIL',
         'email_thread':{'message_key':None,'parent_message_key':None,
                         'reference_keys':[]}}},
        {'document_id':'D-2','name':'SAME.EML','summary':{'document_type':'EMAIL',
         'email_thread':{'message_key':None,'parent_message_key':None,
                         'reference_keys':[]}}},
    ])
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('duplicate Email thread read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-DUPLICATE-THREAD','status':'COMPLETED'},
        'Show me the email thread containing same.eml.',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 analyzed Email files named "same.eml"' in result['answer']
    assert result['citations']==[]


def test_email_thread_question_caps_members_before_read(monkeypatch):
    index={'items':[{'kind':'EMAIL_THREAD','state':'LINKED','warnings':[],
                    'external_reference_count':0,'members':[
        {'document_id':f'D-{value}','file_name':f'{value}.eml','document_type':'EMAIL',
         'role':'MESSAGE','source':'PRIMARY'} for value in range(9)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large Email thread read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-LARGE-THREAD','status':'COMPLETED'},
        'Which emails are in the same thread as 0.eml?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains more than 8 messages' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_email_thread_question(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('parent.eml',['Subject: Coordination']),
        ('reply.eml',['Subject: Re: Coordination']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT DISTINCT e.document_id,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents={row['name']:row['document_id'] for row in rows}
    parent='MSG-'+'a'*24;reply='MSG-'+'b'*24
    summaries={
        'parent.eml':{'document_type':'EMAIL','email_thread':{
            'message_key':parent,'parent_message_key':None,'reference_keys':[]}},
        'reply.eml':{'document_type':'EMAIL','email_thread':{
            'message_key':reply,'parent_message_key':parent,'reference_keys':[parent]}},
    }
    for name,document_id in documents.items():
        db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
            run['id'],document_id,'SUCCESS',json.dumps(summaries[name])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email thread API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],
        'question':'Which emails are in the same thread as reply.eml?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert 'parent.eml; reply.eml' in result['answer']
    assert result['answer_basis']=='WORKFLOW_INDEX' and result['retrieved_count']==0
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('Which imported attachments came from coordination.eml?',
     ('FROM_EMAIL','coordination.eml')),
    ('What attachments were imported from "Coordination Email.eml"?',
     ('FROM_EMAIL','Coordination Email.eml')),
    ('List the selected attachments for coordination.msg.',
     ('FROM_EMAIL','coordination.msg')),
    ('Which email was response.pdf imported from?',('TO_EMAIL','response.pdf')),
    ('Which parent emails are linked to imported attachment "Response Package.pdf"?',
     ('TO_EMAIL','Response Package.pdf')),
    ('What email did imported attachment response.pdf come from?',
     ('TO_EMAIL','response.pdf')),
    ('Show me the parent emails for imported attachment response.pdf.',
     ('TO_EMAIL','response.pdf')),
    ('Which imported attachments came from ../coordination.eml?',None),
    ('Which email was C:response.pdf imported from?',None),
    ('Which email was *.pdf imported from?',None),
    ('Which attachments are in coordination.eml?',None),
    ('Which emails are in the same thread as coordination.eml?',None),
])
def test_email_attachment_relationship_question_boundary(question,expected):
    assert requested_email_attachment_relations(question)==expected
    assert requires_email_attachment_relation_index(question) is bool(expected)


def test_email_attachment_question_lists_selected_imports_without_model_or_evidence_read(
        monkeypatch):
    rows=[
        {'document_id':'MAIL','name':'coordination.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'PDF','name':'response.pdf','summary':{'document_type':'OTHER'}},
        {'document_id':'TXT','name':'note.txt','summary':{'document_type':'OTHER'}},
    ]
    links=[
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL','document_id':'PDF',
         'attachment_index':0,'content_type':'application/pdf'},
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL','document_id':'PDF',
         'attachment_index':0,'content_type':'application/pdf'},
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL','document_id':'TXT',
         'attachment_index':1,'content_type':'text/plain'},
    ]
    index=build_workflow_index(rows,links)
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('attachment question read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment question used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-ATTACHMENTS','status':'COMPLETED'},
        'Which imported attachments came from coordination.eml?',index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='WORKFLOW_INDEX'
    assert ('response.pdf (source attachment index 0; application/pdf; 2 import records)'
            in result['answer'])
    assert 'note.txt (source attachment index 1; text/plain)' in result['answer']
    assert 'do not transfer workflow role, status, approval or authority' in result['answer']
    assert result['citations']==[] and result['retrieved_count']==0


def test_email_attachment_question_lists_every_exact_parent_email(monkeypatch):
    rows=[
        {'document_id':'MAIL-A','name':'a.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'MAIL-B','name':'b.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'PDF','name':'response.pdf','summary':{'document_type':'OTHER'}},
    ]
    links=[
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-A','document_id':'PDF',
         'attachment_index':0,'content_type':'application/pdf'},
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-B','document_id':'PDF',
         'attachment_index':2,'content_type':'application/pdf'},
    ]
    index=build_workflow_index(rows,links)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment question used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-PARENTS','status':'COMPLETED'},
        'Which email was response.pdf imported from?',index)

    assert result['status']=='ANSWERED'
    assert 'a.eml (source attachment index 0; application/pdf)' in result['answer']
    assert 'b.eml (source attachment index 2; application/pdf)' in result['answer']
    assert result['citations']==[] and result['retrieved_count']==0


def test_email_attachment_question_does_not_call_absence_no_attachments(monkeypatch):
    index=build_workflow_index([{
        'document_id':'MAIL','name':'coordination.eml','summary':{'document_type':'EMAIL'}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment question used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-NO-ATTACHMENT','status':'COMPLETED'},
        'Which imported attachments came from coordination.eml?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'no indexed explicitly imported and analyzed attachment relationship' in result['answer']
    assert 'has no attachments' not in result['answer']


def test_email_attachment_question_rejects_duplicate_named_parent_before_read(
        monkeypatch):
    rows=[
        {'document_id':'MAIL-A','name':'same.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'MAIL-B','name':'SAME.EML','summary':{'document_type':'EMAIL'}},
        {'document_id':'PDF-A','name':'a.pdf','summary':{'document_type':'OTHER'}},
        {'document_id':'PDF-B','name':'b.pdf','summary':{'document_type':'OTHER'}},
    ]
    links=[
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-A','document_id':'PDF-A',
         'attachment_index':0,'content_type':'application/pdf'},
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-B','document_id':'PDF-B',
         'attachment_index':0,'content_type':'application/pdf'},
    ]
    index=build_workflow_index(rows,links)
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('duplicate attachment question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-DUPLICATE-PARENT','status':'COMPLETED'},
        'Which imported attachments came from same.eml?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 analyzed Email files named "same.eml"' in result['answer']
    assert result['citations']==[]


def test_email_attachment_question_rejects_duplicate_named_attachment(monkeypatch):
    rows=[
        {'document_id':'MAIL-A','name':'a.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'MAIL-B','name':'b.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'PDF-A','name':'same.pdf','summary':{'document_type':'OTHER'}},
        {'document_id':'PDF-B','name':'SAME.PDF','summary':{'document_type':'OTHER'}},
    ]
    links=[
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-A','document_id':'PDF-A',
         'attachment_index':0,'content_type':'application/pdf'},
        {'source_kind':'EMAIL_ATTACHMENT','source_document_id':'MAIL-B','document_id':'PDF-B',
         'attachment_index':1,'content_type':'application/pdf'},
    ]
    index=build_workflow_index(rows,links)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment question used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-DUPLICATE-ATTACHMENT','status':'COMPLETED'},
        'Which parent emails are linked to imported attachment same.pdf?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'multiple analyzed attachment files named "same.pdf"' in result['answer']
    assert result['citations']==[]


def test_email_attachment_question_caps_selected_imports_before_read(monkeypatch):
    items=[]
    for value in range(9):
        items.append({'kind':'EMAIL_ATTACHMENT','state':'LINKED','attachment_index':value,
                      'content_type':'application/pdf','import_count':1,'members':[
            {'document_id':'MAIL','file_name':'mail.eml','document_type':'EMAIL',
             'role':'MESSAGE','source':'PARENT_EMAIL'},
            {'document_id':f'ATT-{value}','file_name':f'{value}.pdf','document_type':'OTHER',
             'role':'ATTACHMENT','source':'SELECTED_ATTACHMENT'},
        ]})
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large attachment question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-LARGE-ATTACHMENTS','status':'COMPLETED'},
        'Which imported attachments came from mail.eml?',{'items':items})

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 indexed imported attachment relationships' in result['answer']
    assert result['citations']==[]


def test_email_attachment_question_caps_parent_emails_before_read(monkeypatch):
    items=[]
    for value in range(9):
        items.append({'kind':'EMAIL_ATTACHMENT','state':'LINKED','attachment_index':value,
                      'content_type':'application/pdf','import_count':1,'members':[
            {'document_id':f'MAIL-{value}','file_name':f'{value}.eml',
             'document_type':'EMAIL','role':'MESSAGE','source':'PARENT_EMAIL'},
            {'document_id':'ATT','file_name':'response.pdf','document_type':'OTHER',
             'role':'ATTACHMENT','source':'SELECTED_ATTACHMENT'},
        ]})
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('large attachment question read evidence')

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-MANY-PARENTS','status':'COMPLETED'},
        'Which email was response.pdf imported from?',{'items':items})

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 8 indexed parent Email relationships' in result['answer']
    assert result['citations']==[]


def test_email_attachment_question_rejects_malformed_target_relation(monkeypatch):
    index={'items':[{'kind':'EMAIL_ATTACHMENT','state':'LINKED',
                    'attachment_index':0,'content_type':'invalid mime','members':[
        {'document_id':'MAIL','file_name':'mail.eml','document_type':'EMAIL',
         'role':'MESSAGE','source':'PARENT_EMAIL'},
        {'document_id':'ATT','file_name':'response.pdf','document_type':'OTHER',
         'role':'ATTACHMENT','source':'SELECTED_ATTACHMENT'},
    ]}]}
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment question used retrieval'))

    result=ProjectQuestions(object(),object()).ask(
        {'id':'RUN-MALFORMED-ATTACHMENT','status':'COMPLETED'},
        'Which imported attachments came from mail.eml?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'incomplete imported-attachment relationship' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_email_attachment_question(
        client, project, monkeypatch):
    message=EmailMessage();message['Subject']='Coordination';message.set_content('See attachment.')
    message.add_attachment(b'%PDF-synthetic',maintype='application',subtype='pdf',
                           filename='response.pdf')
    source=upload(client,project['id'],'coordination.eml',message.as_bytes())
    attachment=client.get(
        f'/api/documents/{source["document_id"]}/email-attachments').json()['attachments'][0]
    imported=client.post(f'/api/projects/{project["id"]}/email-attachment-imports',json={
        'document_id':source['document_id'],'attachment_index':0,
        'expected_sha256':attachment['sha256']}).json()
    run=client.app.state.runner.create(project['id']);db=client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],source['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL'})))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],imported['document_id'],'SUCCESS',json.dumps({'document_type':'OTHER'})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('attachment API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],
        'question':'Which imported attachments came from coordination.eml?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert 'response.pdf (source attachment index 0; application/pdf)' in result['answer']
    assert result['answer_basis']=='WORKFLOW_INDEX' and result['retrieved_count']==0
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_single_email_headers_answer_locally_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch):
    headers=[
        'Subject: Domestic Water Coordination',
        'From: Alice Architect <alice@example.test>',
        'To: Bob Builder <bob@example.test>',
        'Date: Tue, 5 Jan 2024 10:30:00 -0500',
        'Cc: Carol Checker <carol@example.test>',
        'Bcc: Beth Bidder <beth@example.test>',
        'Reply-To: Erin Engineer <erin@example.test>',
    ]
    run=_source_evidence(client,project,[('coordination.eml',headers)])
    db=client.app.state.db
    rows=db.all('SELECT id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=? LIMIT 1''',(run['id'],))
    index=build_workflow_index([{'document_id':document['document_id'],
        'name':document['name'],'summary':{'document_type':'EMAIL'}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('single Email header used retrieval'))

    cases={
        'What is the email subject?':headers[0],
        'Who sent the email?':headers[1],
        'Who received the email?':headers[2],
        'When was the email sent?':headers[3],
        'Who was copied on the email?':headers[4],
        'Who was blind copied on the email?':headers[5],
        'What is the email Reply-To header?':headers[6],
    }
    for question,quote in cases.items():
        result=ProjectQuestions(db,gateway).ask(run,question,index)
        assert result['status']=='ANSWERED'
        assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
        assert result['citations'][0]['quote']==quote
        assert result['retrieved_count']==1
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_named_email_header_selects_one_file_in_a_multi_email_run(
        client, project, tmp_path, monkeypatch):
    run=_source_evidence(client,project,[
        ('first.eml',['Subject: First Coordination','From: first@example.test']),
        ('second.eml',['Subject: Second Coordination','From: second@example.test',
                       'Cc: checker@example.test','Bcc: bidder@example.test',
                       'Reply-To: engineer@example.test']),
    ])
    db=client.app.state.db
    for row in db.all('SELECT id,payload FROM evidence WHERE run_id=?',(run['id'],)):
        payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    documents=db.all('SELECT id,name FROM documents WHERE project_id=?',(project['id'],))
    index=build_workflow_index([{'document_id':row['id'],'name':row['name'],
                                 'summary':{'document_type':'EMAIL'}} for row in documents])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('named Email header used retrieval'))

    subject=ProjectQuestions(db,gateway).ask(
        run,'What is the subject of email file SECOND.eml?',index)
    sender=ProjectQuestions(db,gateway).ask(run,'Who sent first.eml?',index)
    copied=ProjectQuestions(db,gateway).ask(
        run,'What is the Cc header of second.eml?',index)
    blind=ProjectQuestions(db,gateway).ask(
        run,'Who was blind copied on second.eml?',index)
    reply_to=ProjectQuestions(db,gateway).ask(
        run,'What is the Reply-To header of second.eml?',index)
    missing=ProjectQuestions(db,gateway).ask(
        run,'What is the subject of missing.eml?',index)

    assert subject['status']=='ANSWERED'
    assert subject['citations'][0]['quote']=='Subject: Second Coordination'
    assert 'second.eml' in subject['answer']
    assert sender['status']=='ANSWERED'
    assert sender['citations'][0]['quote']=='From: first@example.test'
    assert copied['status']=='ANSWERED'
    assert copied['citations'][0]['quote']=='Cc: checker@example.test'
    assert blind['status']=='ANSWERED'
    assert blind['citations'][0]['quote']=='Bcc: bidder@example.test'
    assert reply_to['status']=='ANSWERED'
    assert reply_to['citations'][0]['quote']=='Reply-To: engineer@example.test'
    assert missing['status']=='INSUFFICIENT_EVIDENCE'
    assert 'does not contain an analyzed Email file named "missing.eml"' in missing['answer']
    assert requests==[] and db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_named_email_header_blocks_duplicate_exact_file_names_without_evidence_read(
        monkeypatch):
    index={'items':[{'kind':'EMAIL','identifier':None,'members':[
        {'document_id':'D-1','file_name':'duplicate.eml','document_type':'EMAIL'},
        {'document_id':'D-2','file_name':'duplicate.eml','document_type':'EMAIL'},
    ]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('duplicate Email name read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('duplicate Email name used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-DUPLICATE','status':'COMPLETED'},
        'What is the subject of duplicate.eml?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 analyzed Email files named "duplicate.eml"' in result['answer']
    assert result['citations']==[]


def test_unspecified_header_question_blocks_multiple_emails_without_evidence_read(
        monkeypatch):
    index=build_workflow_index([
        {'document_id':'D-1','name':'first.eml','summary':{'document_type':'EMAIL'}},
        {'document_id':'D-2','name':'second.eml','summary':{'document_type':'EMAIL'}},
    ])
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('ambiguous Email header read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('ambiguous Email header used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-EMAILS','status':'COMPLETED'},'What is the email subject?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 Email files' in result['answer']
    assert result['retrieved_count']==0 and result['citations']==[]


def test_single_email_header_ignores_quoted_history_and_blocks_current_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Subject: Current Coordination',
        'Subject: Quoted Earlier Message',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id',(run['id'],))
    for index,row in enumerate(rows):
        payload=json.loads(row['payload'])
        payload['locator']['section']=('EMAIL > HEADERS' if index==0
                                       else 'EMAIL > QUOTED HISTORY')
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=? LIMIT 1''',(run['id'],))
    workflow_index=build_workflow_index([{'document_id':document['document_id'],
        'name':document['name'],'summary':{'document_type':'EMAIL'}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('quoted history used retrieval'))

    current=ProjectQuestions(db,object()).ask(
        run,'What is the email subject?',workflow_index)
    assert current['status']=='ANSWERED'
    assert [item['quote'] for item in current['citations']]==['Subject: Current Coordination']

    second=json.loads(rows[1]['payload']);second['locator']['section']='EMAIL > HEADERS'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(second),rows[1]['id']))
    conflict=ProjectQuestions(db,object()).ask(
        run,'What is the email subject?',workflow_index)
    assert conflict['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting current Subject header values' in conflict['answer']
    assert {item['quote'] for item in conflict['citations']}==set([
        'Subject: Current Coordination','Subject: Quoted Earlier Message'])


def test_reply_to_conflict_uses_public_header_name(client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Reply-To: first@example.test','Reply-To: second@example.test',
    ])])
    db=client.app.state.db
    rows=db.all('SELECT id,payload FROM evidence WHERE run_id=?',(run['id'],))
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=? LIMIT 1''',(run['id'],))
    index=build_workflow_index([{'document_id':document['document_id'],
        'name':document['name'],'summary':{'document_type':'EMAIL'}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Reply-To conflict used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the email Reply-To header?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting current Reply-To header values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Reply-To: first@example.test','Reply-To: second@example.test'}


def test_single_email_header_passage_cap_fails_closed(monkeypatch):
    index=build_workflow_index([
        {'document_id':'D-1','name':'mail.eml','summary':{'document_type':'EMAIL'}},
    ])
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email header cap used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-CAP','status':'COMPLETED'},'What is the email subject?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 current-header passages' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_one_email_header(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('coordination.eml',['Subject: Domestic Water Coordination']),
    ])
    db=client.app.state.db
    row=db.one('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                  JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL'})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email header route used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the email subject?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Subject: Domestic Water Coordination'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_workflow_index_for_a_named_email_in_a_multi_email_run(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('first.eml',['Subject: First Coordination']),
        ('second.eml',['Subject: Second Coordination']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    with db.connect(True) as connection:
        for row in rows:
            payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > HEADERS'
            connection.execute('UPDATE evidence SET payload=? WHERE id=?',
                               (json.dumps(payload),row['id']))
            connection.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
                run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL'})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('named Email API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the subject of second.eml?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Subject: Second Coordination'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_conflict_citation_does_not_borrow_another_workflow_status(
        client, project, tmp_path):
    run=_evidence(client,project,['RFI 42 status: OPEN.\nRFI 43 status: CLOSED.'])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([
        {'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'},
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'CLOSED'},
            ]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))

    result=ProjectQuestions(client.app.state.db,gateway).ask(
        run,'What is the status of RFI 42?',workflow_index)

    sources={item['status']:item['sources'][0]
             for item in result['workflow_conflicts'][0]['statuses']}
    assert sources['OPEN']['citation']['quote']=='RFI 42 status: OPEN.'
    assert 'citation' not in sources['CLOSED']
    assert requests==[]


def test_full_workflow_status_guard_is_exact_and_ignores_role_only_ambiguity():
    role_only=build_workflow_index([
        {'document_id':'D-Q1','name':'question-a.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}},
        {'document_id':'D-Q2','name':'question-b.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}},
    ])
    other_identifier=build_workflow_index([
        {'document_id':'D-43A','name':'rfi-43-open.txt','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'43','role':'RESPONSE','status':'OPEN'}]}},
        {'document_id':'D-43B','name':'rfi-43-closed.txt','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'43','role':'RESPONSE','status':'CLOSED'}]}},
    ])

    assert next(item for item in role_only['items'] if item['kind']=='RFI')['state']=='AMBIGUOUS'
    assert _workflow_index_status_conflicts('What is the status of RFI 42?',role_only)==[]
    assert _workflow_index_status_conflicts('What is the status of RFI 42?',other_identifier)==[]
    assert _workflow_index_status_conflicts('What status does the email report?',other_identifier)==[]
    assert requires_workflow_status_index('What is the status of RFI 42?') is True
    assert requires_workflow_status_index('What does RFI 42 require?') is False


def test_question_api_uses_email_workflow_index_beyond_retrieved_passages(client, project):
    run=_source_evidence(client,project,[
        ('current.eml',['From: architect@example.test\nSubject: RFI 42\nRFI 42 status: OPEN.']),
        ('archive.eml',['Archived coordination note.']),
    ])
    db=client.app.state.db
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    summaries={
        'current.eml':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'}]},
        'archive.eml':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'CLOSED'}]},
    }
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)',[
            (run['id'],documents[name],'SUCCESS',json.dumps(summary))
            for name,summary in summaries.items()])

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the status of RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['retrieved_count']==0 and result['citations']==[]
    assert 'RFI 42' in result['answer']
    assert {source['file_name'] for item in result['workflow_conflicts'][0]['statuses']
            for source in item['sources']}=={'current.eml','archive.eml'}
    sources={source['file_name']:source for item in result['workflow_conflicts'][0]['statuses']
             for source in item['sources']}
    assert sources['current.eml']['citation']['quote']=='RFI 42 status: OPEN.'
    assert 'citation' not in sources['archive.eml']
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_answers_one_exact_email_derived_status_without_retrieval(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('current.eml',['From: architect@example.test\nSubject: RFI 42\nRFI 42 status: OPEN.']),
    ])
    db=client.app.state.db
    document=db.one('''SELECT e.document_id,d.name FROM evidence e
                       JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    summary={'document_type':'EMAIL','workflow_contexts':[{
        'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],document['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('route exact status read evidence'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],
        'question':'Tell me the disposition for Request for Information No. 0042.'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED' and result['answer_basis']=='WORKFLOW_INDEX'
    assert result['retrieved_count']==0 and result['workflow_statuses'][0]['identifier']=='42'
    source=result['workflow_statuses'][0]['statuses'][0]['sources'][0]
    assert source['file_name']=='current.eml'
    assert source['citation']['quote']=='RFI 42 status: OPEN.'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('How many RFIs, Submittals, and Emails are in this run?',('RFI','SUBMITTAL','EMAIL')),
    ('What is the number of requests for information?',('RFI',)),
    ('Count the e-mails in the project.',('EMAIL',)),
    ('How many RFI documents are there?',()),
    ('How many RFIs mention concrete?',()),
    ('How many RFI 42 responses are there?',()),
])
def test_workflow_inventory_question_boundary(question,expected):
    assert requested_workflow_counts(question)==expected
    assert requires_workflow_inventory(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('List all RFIs, Submittals, and Emails in this run.',('RFI','SUBMITTAL','EMAIL')),
    ('Show me the e-mails.',('EMAIL',)),
    ('Which RFIs and Submittals are in this project?',('RFI','SUBMITTAL')),
    ('What Emails are included in the analysis?',('EMAIL',)),
    ('List RFI documents.',()),
    ('List RFI 42 responses.',()),
    ('List RFIs mentioning concrete.',()),
])
def test_workflow_inventory_list_question_boundary(question,expected):
    assert requested_workflow_list(question)==expected
    assert requires_workflow_inventory(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('How many open RFIs are in this run?',('COUNT',('RFI',),'OPEN')),
    ('How many open RFIs?',('COUNT',('RFI',),'OPEN')),
    ('How many RFIs are open?',('COUNT',('RFI',),'OPEN')),
    ('Count the pending Submittals.',('COUNT',('SUBMITTAL',),'PENDING')),
    ('Show me pending Submittals.',('LIST',('SUBMITTAL',),'PENDING')),
    ('List open RFIs.',('LIST',('RFI',),'OPEN')),
    ('List all approved as noted Submittals in this run.',
     ('LIST',('SUBMITTAL',),'APPROVED_AS_NOTED')),
    ('List all Submittals that are approved as noted.',
     ('LIST',('SUBMITTAL',),'APPROVED_AS_NOTED')),
    ('Which rejected Submittals are in this project?',('LIST',('SUBMITTAL',),'REJECTED')),
    ('How many open Emails?',None),
    ('How many approved RFIs?',None),
    ('List closed Submittals.',None),
    ('How many very open RFIs?',None),
    ('List RFI 42 responses.',None),
])
def test_workflow_status_inventory_question_boundary(question,expected):
    assert requested_workflow_status_inventory(question)==expected
    assert requires_workflow_inventory(question) is bool(expected)


def test_workflow_inventory_answer_skips_retrieval_and_live_provider(
        client, project, tmp_path, monkeypatch):
    run=_evidence(client,project,['RFI 42 question.'])
    index=build_workflow_index([
        {'document_id':'MAIL','name':'question.eml','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}},
        {'document_id':'RESPONSE','name':'response.pdf','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}},
        {'document_id':'REFERENCE','name':'minutes.txt','summary':{
            'document_type':'OTHER','workflow_references':[
                {'workflow_type':'RFI','identifier':'99'}]}},
        {'document_id':'SUBMITTAL','name':'submittal.pdf','summary':{
            'document_type':'SUBMITTAL','workflow_contexts':[
                {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
                 'status':'PENDING'}]}},
        {'document_id':'EMAIL','name':'coordination.eml','summary':{'document_type':'EMAIL'}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))
    service=ProjectQuestions(client.app.state.db,gateway)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('inventory answer read evidence'))

    result=service.ask(
        run,'How many RFIs, Submittals, and Emails are in this run?',index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='WORKFLOW_INDEX'
    assert result['answer']==('The complete workflow index for the selected analysis run contains '
                              '2 RFI identifiers, 1 Submittal identifier, and 2 Email files.')
    assert result['workflow_counts']==[
        {'kind':'RFI','count':2},{'kind':'SUBMITTAL','count':1},{'kind':'EMAIL','count':2}]
    assert result['retrieved_count']==0 and result['citations']==[]
    listed=service.ask(run,'List all RFIs, Submittals, and Emails in this run.',index)
    assert listed['answer']==(
        'The complete workflow index for the selected analysis run contains:\n'
        'RFI identifiers (2): 42, 99.\n'
        'Submittal identifier (1): 23-01.\n'
        'Email files (2): coordination.eml, question.eml.')
    assert listed['workflow_inventory']==[
        {'kind':'RFI','total':2,'values':['42','99'],'truncated':False},
        {'kind':'SUBMITTAL','total':1,'values':['23-01'],'truncated':False},
        {'kind':'EMAIL','total':2,'values':['coordination.eml','question.eml'],'truncated':False},
    ]
    large=build_workflow_index([
        {'document_id':f'RFI-{number}','name':f'rfi-{number}.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[
                {'workflow_type':'RFI','identifier':str(number),'role':'QUESTION','status':None}]}}
        for number in range(1,56)])
    large_result=service.ask(run,'List all RFIs in this run.',large)
    assert large_result['workflow_inventory'][0]['total']==55
    assert len(large_result['workflow_inventory'][0]['values'])==50
    assert large_result['workflow_inventory'][0]['truncated'] is True
    assert '55; first 50 shown' in large_result['answer']
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_status_inventory_uses_explicit_status_and_preserves_conflicts(
        client, project, tmp_path, monkeypatch):
    run=_evidence(client,project,['RFI and Submittal status inventory.'])
    rows=[
        ('MAIL-1','rfi-1.eml','RFI','1','RESPONSE','OPEN','EMAIL'),
        ('RFI-2','rfi-2.pdf','RFI','2','RESPONSE','CLOSED','RFI_RESPONSE'),
        ('RFI-3A','rfi-3-open.pdf','RFI','3','RESPONSE','OPEN','RFI_RESPONSE'),
        ('RFI-3B','rfi-3-closed.pdf','RFI','3','RESPONSE','CLOSED','RFI_RESPONSE'),
        ('RFI-4','rfi-4.pdf','RFI','4','RESPONSE','OPEN FOR REVIEW','RFI_RESPONSE'),
        ('RFI-5','rfi-5.pdf','RFI','5','RESPONSE','OPEN CLOSED','RFI_RESPONSE'),
        ('SUB-1','sub-1.pdf','SUBMITTAL','23-01','SUBMITTAL','PENDING','SUBMITTAL'),
        ('SUB-2','sub-2.pdf','SUBMITTAL','23-02','SUBMITTAL','SUBMITTED','SUBMITTAL'),
        ('SUB-3','sub-3.pdf','SUBMITTAL','23-03','SUBMITTAL','APPROVED AS NOTED','SUBMITTAL'),
        ('SUB-4','sub-4.pdf','SUBMITTAL','23-04','SUBMITTAL','APPROVED','SUBMITTAL'),
        ('SUB-5A','sub-5-approved.pdf','SUBMITTAL','23-05','SUBMITTAL','APPROVED','SUBMITTAL'),
        ('SUB-5B','sub-5-rejected.pdf','SUBMITTAL','23-05','SUBMITTAL','REJECTED','SUBMITTAL'),
    ]
    index=build_workflow_index([
        {'document_id':document_id,'name':name,'summary':{
            'document_type':document_type,'workflow_contexts':[{
                'workflow_type':kind,'identifier':identifier,'role':role,'status':status}]}}
        for document_id,name,kind,identifier,role,status,document_type in rows])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))
    service=ProjectQuestions(client.app.state.db,gateway)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('status inventory read evidence'))

    opened=service.ask(run,'How many open RFIs are in this run?',index)
    assert opened['workflow_status_inventory']==[
        {'kind':'RFI','status':'OPEN','count':2,'ambiguous_count':2}]
    assert '2 RFI identifiers' in opened['answer'] and 'excluded as ambiguous' in opened['answer']
    listed=service.ask(run,'List all open RFIs in this run.',index)
    assert listed['workflow_status_inventory']==[{
        'kind':'RFI','status':'OPEN','count':2,'ambiguous_count':2,
        'values':['1','4'],'truncated':False,
        'ambiguous_values':['3','5'],'ambiguous_truncated':False}]
    approved=service.ask(run,'How many approved Submittals are in this run?',index)
    assert approved['workflow_status_inventory']==[
        {'kind':'SUBMITTAL','status':'APPROVED','count':2,'ambiguous_count':1}]
    approved_as_noted=service.ask(
        run,'List all approved as noted Submittals in this run.',index)
    assert approved_as_noted['workflow_status_inventory'][0]['values']==['23-03']
    assert approved_as_noted['workflow_status_inventory'][0]['ambiguous_count']==0
    pending=service.ask(run,'Count the pending Submittals.',index)
    assert pending['workflow_status_inventory'][0]['count']==2
    large=build_workflow_index([
        {'document_id':f'OPEN-{number}','name':f'open-{number}.pdf','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':str(number),'role':'RESPONSE','status':'OPEN'}]}}
        for number in range(1,56)])
    large_result=service.ask(run,'List all open RFIs in this run.',large)
    assert large_result['workflow_status_inventory'][0]['count']==55
    assert len(large_result['workflow_status_inventory'][0]['values'])==50
    assert large_result['workflow_status_inventory'][0]['truncated'] is True
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_complete_workflow_index_for_inventory_count(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('question.eml',['RFI 42 question.']),('response.pdf',['RFI 42 response.']),
        ('submittal.pdf',['Submittal 23-01 pending.']),('coordination.eml',['Coordination.']),
    ])
    db=client.app.state.db
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    summaries={
        'question.eml':{'document_type':'EMAIL','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]},
        'response.pdf':{'document_type':'RFI_RESPONSE','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]},
        'submittal.pdf':{'document_type':'SUBMITTAL','workflow_contexts':[
            {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]},
        'coordination.eml':{'document_type':'EMAIL'},
    }
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)',[
            (run['id'],documents[name],'SUCCESS',json.dumps(summary))
            for name,summary in summaries.items()])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('inventory answer read evidence'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'How many RFIs, Submittals, and Emails are in this run?'})

    assert response.status_code==200
    result=response.json()
    assert result['workflow_counts']==[
        {'kind':'RFI','count':1},{'kind':'SUBMITTAL','count':1},{'kind':'EMAIL','count':2}]
    assert result['retrieved_count']==0 and result['answer_basis']=='WORKFLOW_INDEX'
    listed=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'List all RFIs, Submittals, and Emails in this run.'})
    assert listed.status_code==200
    assert listed.json()['workflow_inventory']==[
        {'kind':'RFI','total':1,'values':['42'],'truncated':False},
        {'kind':'SUBMITTAL','total':1,'values':['23-01'],'truncated':False},
        {'kind':'EMAIL','total':2,'values':['coordination.eml','question.eml'],'truncated':False},
    ]
    filtered=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'How many open RFIs are in this run?'})
    assert filtered.status_code==200
    assert filtered.json()['workflow_status_inventory']==[
        {'kind':'RFI','status':'OPEN','count':0,'ambiguous_count':1}]


@pytest.mark.parametrize(('question','expected'),[
    ('Who is assigned to RFI 42?',('RFI','42','ASSIGNED_TO')),
    ('Who is the assignee for Request for Information No. 0042?',
     ('RFI','42','ASSIGNED_TO')),
    ('What is the Assigned To field for RFI ARC-42?',
     ('RFI','ARC-42','ASSIGNED_TO')),
    ('Who is responsible for Submittal MEP-023?',
     ('SUBMITTAL','MEP-023','RESPONSIBLE_PARTY')),
    ('What is the Responsible Party for Submission 23 05 00 - 01?',
     ('SUBMITTAL','23 05 00-01','RESPONSIBLE_PARTY')),
    ('Who submitted Submittal 23-01?',('SUBMITTAL','23-01','SUBMITTED_BY')),
    ('What is the Submitted By field of Submittal 23-01?',
     ('SUBMITTAL','23-01','SUBMITTED_BY')),
    ('Who is the reviewer for Submittal 23-01?',
     ('SUBMITTAL','23-01','REVIEWED_BY')),
    ('Who should be assigned to RFI 42?',None),
    ('Who is responsible for closing RFI 42?',None),
    ('Who is assigned to RFI 42 and RFI 43?',None),
    ('Who sent coordination.eml?',None),
])
def test_exact_workflow_party_question_boundary(question,expected):
    assert requested_workflow_party(question)==expected
    assert requires_workflow_party_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source'),[
    ('RFI-42-question.pdf','RFI_QUESTION',
     {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'},
     'RFI 42 > QUESTION','Who is assigned to RFI 42?','Assigned To: Project Architect'),
    ('RFI-42-response.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','Who is responsible for Request for Information No. 0042?',
     'Responsible: Mechanical Contractor'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','Who submitted Submittal 23-01?',
     'Submitter: Acme Mechanical'),
    ('Submittal-23-01-review.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'APPROVED'},
     'SUBMITTAL 23-01','Who is the reviewer for Submittal 23-01?',
     'Reviewer: Jane Reviewer'),
])
def test_exact_workflow_party_answers_from_explicit_field_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context, section,
        question, source):
    run=_source_evidence(client,project,[(file_name,[source])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('party field used ordinary retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source
    assert source.split(': ',1)[1] in result['answer']
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_party_merges_same_value_and_blocks_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Assigned To: Project Architect']),
        ('RFI-42-response.pdf',['Assignee: project architect']),
        ('RFI-42-email.eml',['Assigned To: General Contractor']),
    ])
    db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'EMAIL' if row['name'].endswith('.eml') else 'RFI_RESPONSE',
            'workflow_contexts':[{'workflow_type':'RFI','identifier':'42','role':'RESPONSE',
                                  'status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('party conflict used retrieval'))

    merged=ProjectQuestions(db,object()).ask(
        run,'Who is assigned to RFI 42?',build_workflow_index(contexts[:2]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'Who is assigned to RFI 42?',build_workflow_index(contexts))

    assert merged['status']=='ANSWERED' and len(merged['citations'])==2
    assert 'Project Architect' in merged['answer']
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in conflicted['answer']
    assert {item['quote'] for item in conflicted['citations']}=={
        'Assigned To: Project Architect','Assignee: project architect',
        'Assigned To: General Contractor'}


@pytest.mark.parametrize(('section','source'),[
    ('RFI 42 > RESPONSE','From: architect@example.test\nTo: contractor@example.test'),
    ('RFI 42 > RESPONSE','Ball in Court: Project Architect'),
    ('RFI 42 > RESPONSE','The contractor is responsible for coordination.'),
    ('EMAIL > QUOTED HISTORY > RFI 42','Responsible Party: Project Architect'),
    ('EMAIL > SIGNATURE > RFI 42','Responsible Party: Project Architect'),
    ('RFI 43 > RESPONSE','Responsible Party: Project Architect'),
    ('RFI 42 > RESPONSE','Responsible Party: RFI 43 reviewer'),
])
def test_exact_workflow_party_does_not_infer_or_cross_scope(
        client, project, monkeypatch, section, source):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[source])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42-response.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    calls=[]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:(calls.append('retrieval') or []))

    result=ProjectQuestions(db,object()).ask(run,'Who is responsible for RFI 42?',index)

    assert calls==['retrieval'] and result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_party_accepts_current_email_body(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Assigned To: Project Architect',
    ])])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY > RFI 42'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('current Email party used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'Who is assigned to RFI 42?',index)

    assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']=='Assigned To: Project Architect'


def test_exact_workflow_party_primary_document_cap_fails_before_evidence_read(monkeypatch):
    index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('party document cap read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('party document cap used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-PARTY-CAP','status':'COMPLETED'},'Who is assigned to RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in result['answer']
    assert result['citations']==[]


def test_exact_workflow_party_passage_cap_fails_closed(monkeypatch):
    index={'items':[{'kind':'SUBMITTAL','identifier':'23-01','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('party passage cap used retrieval'))

    result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-PARTY-CAP','status':'COMPLETED'},
        'Who submitted Submittal 23-01?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in result['answer']
    assert result['citations']==[]


def test_question_api_loads_workflow_index_for_exact_workflow_party(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('Submittal-23-01.pdf',['Reviewed By: Project Architect']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
        'status':'APPROVED'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('party API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'Who reviewed Submittal 23-01?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']=='Reviewed By: Project Architect'
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_party_parser_preserves_identity_role_and_complete_value():
    source=('RFI 42\nAssigned To: Project Architect\n'
            'RFI 43\nAssigned To: General Contractor')

    assert _workflow_party_source_values(source)=={
        (('RFI','42'),'ASSIGNED_TO','project architect'),
        (('RFI','43'),'ASSIGNED_TO','general contractor')}
    assert _workflow_party_claim_values(
        'RFI 42 Assigned To: “Project Architect”.')=={
            (('RFI','42'),'ASSIGNED_TO','project architect')}


def test_answer_accepts_same_exact_workflow_party_field(client, project):
    source='Assigned To: Project Architect'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='Who is assigned to RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > QUESTION'
    grounded={'status':'ANSWERED','answer':'RFI 42 Assigned To: “Project Architect”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


@pytest.mark.parametrize(('source','answer','error'),[
    ('Assigned To: Project Architect',
     'RFI 42 Assigned To: “General Contractor”.','workflow party field absent'),
    ('Submitted By: Acme Mechanical',
     'Submittal 23-01 Reviewed By: “Acme Mechanical”.','workflow party field absent'),
    ('Ball in Court: Project Architect',
     'RFI 42 Assigned To: “Project Architect”.','workflow party field absent'),
    ('The contractor is responsible for coordination.',
     'RFI 42 Responsible Party: “Contractor”.','workflow party field absent'),
])
def test_answer_rejects_inferred_or_role_swapped_workflow_party(
        client, project, source, answer, error):
    identity=('SUBMITTAL 23-01' if answer.startswith('Submittal') else 'RFI 42')
    question=('Who reviewed Submittal 23-01?' if answer.startswith('Submittal')
              else 'Who is assigned to RFI 42?')
    run=_source_evidence(client,project,[('workflow.pdf',[source])])
    row=client.app.state.db.one(
        '''SELECT e.payload,d.name FROM evidence e JOIN documents d ON d.id=e.document_id
           WHERE e.run_id=?''',(run['id'],))
    item=json.loads(row['payload']);item['file_name']=row['name']
    item['locator']['section']=identity;evidence=[item]
    wrong={'status':'ANSWERED','answer':answer,'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match=error):
        validate_answer_model(wrong,evidence,question)


def test_answer_rejects_workflow_party_borrowed_from_another_item(client, project):
    source=('RFI 42\nAssigned To: Project Architect\n'
            'RFI 43\nAssigned To: General Contractor')
    run=_source_evidence(client,project,[('RFI-log.pdf',[source])])
    question='Who is assigned to RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={'status':'ANSWERED','answer':'RFI 42 Assigned To: “General Contractor”.',
           'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow party field absent'):
        validate_answer_model(wrong,evidence,question)


def test_answer_rejects_conflicting_workflow_party_values(client, project):
    source='Assigned To: Project Architect\nAssigned To: General Contractor'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='Who is assigned to RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > QUESTION'
    selected={'status':'ANSWERED','answer':'RFI 42 Assigned To: “Project Architect”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='conflicting workflow party field values'):
        validate_answer_model(selected,evidence,question)


def test_workflow_party_question_requires_explicit_scoped_field(client, project):
    source='Assigned To: Project Architect'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='Who is assigned to RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > QUESTION'
    evasive={'status':'ANSWERED','answer':'The project architect is listed.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires an explicit scoped field'):
        validate_answer_model(evasive,evidence,question)


def test_workflow_party_source_finding_cannot_borrow_another_source_value(
        client, project):
    specification='Assigned To: Specification Coordinator'
    rfi='Assigned To: Project Architect'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[specification]),('RFI-42.pdf',[rfi])])
    question='Compare the specification and RFI 42 assigned parties.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    for item in evidence:
        if item['file_name']=='RFI-42.pdf':item['locator']['section']='RFI 42 > QUESTION'
    wrong={
        'status':'ANSWERED','answer':'The sources list different parties.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification contains an assignment field.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':specification}]},
            {'source_type':'RFI','file_name':'RFI-42.pdf',
             'statement':'RFI 42 Assigned To: “Specification Coordinator”.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':rfi}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains a workflow party field'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('question','expected'),[
    ('What action is required for RFI 42?',('RFI','42','ACTION_REQUIRED')),
    ('What is the Required Action field for Request for Information No. 0042?',
     ('RFI','42','ACTION_REQUIRED')),
    ('What is the next action for Submittal 23-01?',
     ('SUBMITTAL','23-01','NEXT_ACTION')),
    ('What is the action item for Submission MEP-023?',
     ('SUBMITTAL','MEP-023','ACTION_ITEM')),
    ('What should we do for RFI 42?',None),
    ('What action is required for RFI 42 and RFI 43?',None),
    ('What are the action items for Submittal 23-01?',None),
    ('What action is required in the email?',None),
])
def test_exact_workflow_action_question_boundary(question,expected):
    assert requested_workflow_action(question)==expected
    assert requires_workflow_action_index(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('What action is required in the email?',('ACTION_REQUIRED',None)),
    ('What is the Required Action field for this email?',('ACTION_REQUIRED',None)),
    ('What is the next action in the email?',('NEXT_ACTION',None)),
    ('What is the action item for this email?',('ACTION_ITEM',None)),
    ('What is the next action in email file second.eml?',('NEXT_ACTION','second.eml')),
    ('What is the action item for "coordination note.eml"?',
     ('ACTION_ITEM','coordination note.eml')),
    ('What action is required in C:\\mail\\coordination.eml?',None),
    ('What action is required in *.eml?',None),
    ('What action is required in first.eml and second.eml?',None),
    ('What action is required for RFI 42?',None),
])
def test_exact_email_action_question_boundary(question,expected):
    assert requested_email_action(question)==expected
    assert requires_email_action_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source'),[
    ('RFI-42.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','What action is required for RFI 42?',
     'Action Required: Issue revised detail A5.2'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','What is the next action for Submittal 23-01?',
     'Next Action: Contractor to resubmit product data'),
    ('Submittal-MEP-023.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'MEP-023','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL MEP-023','What is the action item for Submittal MEP-023?',
     'Action Item: Verify motor voltage before release'),
])
def test_exact_workflow_action_answers_from_explicit_field_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context, section,
        question, source):
    run=_source_evidence(client,project,[(file_name,[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('action field used ordinary retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source
    assert source.split(': ',1)[1] in result['answer']
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_action_merges_same_value_and_blocks_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Action Required: Issue revised detail A5.2']),
        ('RFI-42-response.pdf',['Required Action: issue revised detail a5.2']),
        ('RFI-42-addendum.pdf',['Action Required: Submit structural calculation']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'RFI_RESPONSE',
            'workflow_contexts':[{'workflow_type':'RFI','identifier':'42','role':'RESPONSE',
                                  'status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('action conflict used retrieval'))

    merged=ProjectQuestions(db,object()).ask(
        run,'What action is required for RFI 42?',build_workflow_index(contexts[:2]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'What action is required for RFI 42?',build_workflow_index(contexts))

    assert merged['status']=='ANSWERED' and len(merged['citations'])==2
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in conflicted['answer']
    assert len(conflicted['citations'])==3


@pytest.mark.parametrize(('section','source'),[
    ('RFI 42 > RESPONSE','The contractor shall issue a revised detail.'),
    ('RFI 42 > RESPONSE','Response: Issue a revised detail.'),
    ('EMAIL > HEADERS > RFI 42','Action Required: Issue revised detail A5.2'),
    ('EMAIL > QUOTED HISTORY > RFI 42','Action Required: Issue revised detail A5.2'),
    ('EMAIL > SIGNATURE > RFI 42','Action Required: Issue revised detail A5.2'),
    ('RFI 43 > RESPONSE','Action Required: Issue revised detail A5.2'),
])
def test_exact_workflow_action_does_not_infer_or_cross_scope(
        client, project, monkeypatch, section, source):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42-response.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
        lambda *_args,**_kwargs:pytest.fail('unsupported explicit action used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What action is required for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[]


def test_exact_workflow_action_accepts_current_email_body(client, project, monkeypatch):
    source='Action Required: Issue revised detail A5.2'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY > RFI 42'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('current Email action used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What action is required for RFI 42?',index)

    assert result['status']=='ANSWERED' and result['citations'][0]['quote']==source


def test_workflow_action_parser_preserves_identity_role_and_complete_value():
    source=('RFI 42\nAction Required: Issue revised detail A5.2\n'
            'RFI 43\nNext Action: Submit structural calculation')

    assert _workflow_action_source_values(source)=={
        (('RFI','42'),'ACTION_REQUIRED','issue revised detail a5.2'),
        (('RFI','43'),'NEXT_ACTION','submit structural calculation')}
    assert _workflow_action_claim_values(
        'RFI 42 Action Required: “Issue revised detail A5.2”.')=={
            (('RFI','42'),'ACTION_REQUIRED','issue revised detail a5.2')}


def test_question_api_loads_workflow_index_for_exact_workflow_action(
        client, project, monkeypatch):
    source='Action Item: Verify motor voltage before release'
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('action API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the action item for Submittal 23-01?'})

    assert response.status_code==200
    result=response.json();assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']==source
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_answer_accepts_same_exact_workflow_action_field(client, project):
    source='Action Required: Issue revised detail A5.2'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])]);question='What action is required for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    grounded={'status':'ANSWERED','answer':'RFI 42 Action Required: “Issue revised detail A5.2”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


@pytest.mark.parametrize(('source','answer'),[
    ('Action Required: Issue revised detail A5.2',
     'RFI 42 Action Required: “Submit structural calculation”.'),
    ('Next Action: Issue revised detail A5.2',
     'RFI 42 Action Required: “Issue revised detail A5.2”.'),
    ('The contractor shall issue a revised detail.',
     'RFI 42 Action Required: “Issue a revised detail”.'),
])
def test_answer_rejects_inferred_or_role_swapped_workflow_action(
        client, project, source, answer):
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])]);question='What action is required for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    wrong={'status':'ANSWERED','answer':answer,'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow action field absent'):
        validate_answer_model(wrong,evidence,question)


def test_workflow_action_question_requires_explicit_scoped_field(client, project):
    source='Action Required: Issue revised detail A5.2'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])]);question='What action is required for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    evasive={'status':'ANSWERED','answer':'A revised detail is listed.','source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires an explicit scoped field'):
        validate_answer_model(evasive,evidence,question)

    email_label={**evasive,
                 'answer':'RFI 42 Email Action Required: “Issue revised detail A5.2”.'}
    with pytest.raises(ValueError,match='Email action field absent'):
        validate_answer_model(email_label,evidence,question)


def test_email_actions_answer_only_from_current_body_and_named_file(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('first.eml',['Action Required: Ignore quoted request']),
        ('second.eml',['Next Action: Submit revised coordination drawing']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    documents=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']=(
            'EMAIL > QUOTED HISTORY' if row['name']=='first.eml' else 'EMAIL > BODY')
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        documents.append({'document_id':row['document_id'],'name':row['name'],
                          'summary':{'document_type':'EMAIL'}})
    index=build_workflow_index(documents)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email action used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the next action in email file SECOND.eml?',index)
    ambiguous=ProjectQuestions(db,object()).ask(run,'What is the next action in the email?',index)

    assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']=='Next Action: Submit revised coordination drawing'
    assert ambiguous['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 Email files' in ambiguous['answer']


def test_email_action_blocks_non_body_sections_and_current_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[('coordination.eml',[
        'Action Required: First current action','Required Action: Second current action',
        'Action Required: Header action','Action Required: Quoted action',
    ])]);db=client.app.state.db
    rows=db.all('SELECT id,document_id,payload FROM evidence WHERE run_id=? ORDER BY id',(run['id'],))
    sections=['EMAIL > BODY','EMAIL > BODY','EMAIL > HEADERS','EMAIL > QUOTED HISTORY']
    for row,section in zip(rows,sections):
        payload=json.loads(row['payload']);payload['locator']['section']=section
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':rows[0]['document_id'],'name':'coordination.eml',
                                'summary':{'document_type':'EMAIL'}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email action conflict used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What action is required in the email?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting current Action Required values' in result['answer']
    assert {item['quote'] for item in result['citations']}=={
        'Action Required: First current action','Required Action: Second current action'}


def test_question_api_loads_workflow_index_for_email_action(client, project, monkeypatch):
    source='Action Item: Confirm access before mobilization'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
        run['id'],row['document_id'],'SUCCESS',json.dumps({'document_type':'EMAIL'})))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email action API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the action item for the email?'})

    assert response.status_code==200
    result=response.json();assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']==source
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_email_action_parser_preserves_role_and_complete_value():
    assert _email_action_source_values(
        'Next Action: Submit revised coordination drawing')=={
            ('NEXT_ACTION','submit revised coordination drawing')}
    assert _email_action_claim_values(
        'Email Next Action: “Submit revised coordination drawing”.')=={
            ('NEXT_ACTION','submit revised coordination drawing')}


def test_answer_accepts_email_action_only_from_current_body(client, project):
    source='Next Action: Submit revised coordination drawing'
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the next action in the email?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='EMAIL > BODY'
    grounded={'status':'ANSWERED',
              'answer':'Email Next Action: “Submit revised coordination drawing”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)

    evidence[0]['locator']['section']='EMAIL > QUOTED HISTORY'
    with pytest.raises(ValueError,match='Email action field absent'):
        validate_answer_model(grounded,evidence,question)


def test_email_action_rejects_role_swap_and_requires_explicit_field(client, project):
    source='Next Action: Submit revised coordination drawing'
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the next action in the email?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='EMAIL > BODY'
    swapped={'status':'ANSWERED',
             'answer':'Email Action Required: “Submit revised coordination drawing”.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}
    evasive={'status':'ANSWERED','answer':'The evidence lists a next step.',
             'source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='Email action field absent'):
        validate_answer_model(swapped,evidence,question)
    with pytest.raises(ValueError,match='requires an explicit current-body field'):
        validate_answer_model(evasive,evidence,question)


def test_workflow_action_caps_fail_before_unbounded_work(monkeypatch):
    primary_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    passage_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('action primary-file cap read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('action cap used retrieval'))

    primary=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-ACTION-CAP','status':'COMPLETED'},
        'What action is required for RFI 42?',primary_index)
    passages=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-ACTION-CAP','status':'COMPLETED'},
        'What action is required for RFI 42?',passage_index)

    assert primary['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in primary['answer']
    assert passages['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in passages['answer']


def test_email_action_named_selection_and_passage_cap_fail_closed(monkeypatch):
    duplicate={'items':[{'kind':'EMAIL','identifier':None,'members':[
        {'document_id':'D-1','file_name':'duplicate.eml','document_type':'EMAIL'},
        {'document_id':'D-2','file_name':'duplicate.eml','document_type':'EMAIL'}]}]}
    one=build_workflow_index([
        {'document_id':'D-1','name':'mail.eml','summary':{'document_type':'EMAIL'}}])
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('duplicate Email action read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('Email action cap used retrieval'))

    duplicate_result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-EMAIL-ACTION','status':'COMPLETED'},
        'What action is required in duplicate.eml?',duplicate)
    passage_result=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-EMAIL-ACTION','status':'COMPLETED'},
        'What action is required in the email?',one)

    assert duplicate_result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'contains 2 analyzed Email files named "duplicate.eml"' in duplicate_result['answer']
    assert passage_result['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 current Email-body passages' in passage_result['answer']


def test_answer_rejects_cross_item_and_conflicting_workflow_action_values(client, project):
    source=('RFI 42\nAction Required: Issue revised detail A5.2\n'
            'RFI 43\nAction Required: Submit structural calculation')
    run=_source_evidence(client,project,[('RFI-log.pdf',[source])])
    question='What action is required for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    borrowed={'status':'ANSWERED',
              'answer':'RFI 42 Action Required: “Submit structural calculation”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='workflow action field absent'):
        validate_answer_model(borrowed,evidence,question)

    conflict='Action Required: First action\nRequired Action: Second action'
    conflict_run=_source_evidence(client,project,[('RFI-42.pdf',[conflict])])
    conflict_evidence=retrieve_evidence(client.app.state.db,conflict_run,question)
    conflict_evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    selected={'status':'ANSWERED','answer':'RFI 42 Action Required: “First action”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':conflict}]}
    with pytest.raises(ValueError,match='conflicting workflow action field values'):
        validate_answer_model(selected,conflict_evidence,question)


def test_answer_rejects_conflicting_email_action_values(client, project):
    source='Next Action: First action\nNext Action: Second action'
    run=_source_evidence(client,project,[('coordination.eml',[source])])
    question='What is the next action in the email?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='EMAIL > BODY'
    selected={'status':'ANSWERED','answer':'Email Next Action: “First action”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='conflicting Email action field values'):
        validate_answer_model(selected,evidence,question)


def test_named_email_action_question_cannot_borrow_another_file(client, project):
    first='Next Action: Submit revised coordination drawing'
    second='Next Action: Confirm access before mobilization'
    run=_source_evidence(client,project,[('first.eml',[first]),('second.eml',[second])])
    question='What is the next action in second.eml?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    for item in evidence:item['locator']['section']='EMAIL > BODY'
    first_evidence=next(item for item in evidence if item['file_name']=='first.eml')
    borrowed={'status':'ANSWERED',
              'answer':'Email Next Action: “Submit revised coordination drawing”.',
              'source_findings':[],
              'citations':[{'evidence_id':first_evidence['evidence_id'],'quote':first}]}

    with pytest.raises(ValueError,match='requires an explicit current-body field'):
        validate_answer_model(borrowed,evidence,question)


@pytest.mark.parametrize(('question','expected'),[
    ('Who has the ball in court for RFI 42?',('RFI','42','BALL_IN_COURT')),
    ('What is the Ball in Court field for Request for Information No. 0042?',
     ('RFI','42','BALL_IN_COURT')),
    ('What is the priority of RFI ARC-42?',('RFI','ARC-42','PRIORITY')),
    ('Show me the Discipline field for Submittal 23-01.',
     ('SUBMITTAL','23-01','DISCIPLINE')),
    ('What is the location for Submission MEP-023?',
     ('SUBMITTAL','MEP-023','LOCATION')),
    ('Who should have the ball in court for RFI 42?',None),
    ('What is the priority of RFI 42 and RFI 43?',None),
    ('Where is RFI 42 located?',None),
    ('What is the email priority?',None),
])
def test_exact_workflow_metadata_question_boundary(question,expected):
    assert requested_workflow_metadata(question)==expected
    assert requires_workflow_metadata_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source'),[
    ('RFI-42.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','Who has the ball in court for RFI 42?',
     'Ball in Court: Project Architect'),
    ('RFI-ARC-42.pdf','RFI_QUESTION',
     {'workflow_type':'RFI','identifier':'ARC-42','role':'QUESTION','status':'OPEN'},
     'RFI ARC-42 > QUESTION','What is the priority of RFI ARC-42?',
     'Priority: High'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','What is the discipline for Submittal 23-01?',
     'Discipline: Mechanical'),
    ('Submittal-MEP-023.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'MEP-023','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL MEP-023','What is the location for Submittal MEP-023?',
     'Location: Level 2 Mechanical Room'),
])
def test_exact_workflow_metadata_answers_from_explicit_field_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context, section,
        question, source):
    run=_source_evidence(client,project,[(file_name,[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
        lambda *_args,**_kwargs:pytest.fail('workflow metadata used ordinary retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source
    assert source.split(': ',1)[1] in result['answer']
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_metadata_merges_alias_format_and_blocks_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Ball in Court: Project Architect']),
        ('RFI-42-response.pdf',['Ball-In-Court: project architect']),
        ('RFI-42-addendum.pdf',['Ball in Court: General Contractor']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('metadata conflict used retrieval'))

    merged=ProjectQuestions(db,object()).ask(
        run,'Who has the ball in court for RFI 42?',build_workflow_index(contexts[:2]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'Who has the ball in court for RFI 42?',build_workflow_index(contexts))

    assert merged['status']=='ANSWERED' and len(merged['citations'])==2
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in conflicted['answer']
    assert len(conflicted['citations'])==3


@pytest.mark.parametrize(('section','source'),[
    ('RFI 42 > RESPONSE','The project architect currently has the ball.'),
    ('RFI 42 > RESPONSE','Assigned To: Project Architect'),
    ('RFI 42 > RESPONSE','Email Priority: High'),
    ('EMAIL > HEADERS > RFI 42','Priority: High'),
    ('EMAIL > QUOTED HISTORY > RFI 42','Priority: High'),
    ('EMAIL > SIGNATURE > RFI 42','Priority: High'),
    ('RFI 43 > RESPONSE','Priority: High'),
])
def test_exact_workflow_metadata_does_not_infer_or_cross_scope(
        client, project, monkeypatch, section, source):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42-response.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
        lambda *_args,**_kwargs:pytest.fail('unsupported metadata used ordinary retrieval'))
    question=('Who has the ball in court for RFI 42?'
              if 'ball' in source.casefold() or source.startswith('Assigned')
              else 'What is the priority of RFI 42?')

    result=ProjectQuestions(db,object()).ask(run,question,index)

    assert result['status']=='INSUFFICIENT_EVIDENCE' and result['citations']==[]


def test_exact_workflow_metadata_accepts_current_email_body(client, project, monkeypatch):
    source='Priority: Urgent'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY > RFI 42'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
        lambda *_args,**_kwargs:pytest.fail('current Email metadata used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What is the priority of RFI 42?',index)

    assert result['status']=='ANSWERED' and result['citations'][0]['quote']==source


def test_workflow_metadata_caps_fail_before_unbounded_work(monkeypatch):
    primary_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    passage_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('metadata primary-file cap read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('metadata cap used retrieval'))

    primary=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-METADATA-CAP','status':'COMPLETED'},
        'What is the priority of RFI 42?',primary_index)
    passages=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-METADATA-CAP','status':'COMPLETED'},
        'What is the priority of RFI 42?',passage_index)

    assert primary['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 primary files' in primary['answer']
    assert passages['status']=='INSUFFICIENT_EVIDENCE'
    assert 'more than 32 candidate source passages' in passages['answer']


@pytest.mark.parametrize('question',[
    'What action is required for RFI 42?',
    'What is the priority of RFI 42?',
    'What action is required in the email?',
])
def test_strict_action_and_metadata_questions_fail_closed_without_indexed_primary_source(
        monkeypatch, question):
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('missing indexed source read evidence')
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('missing indexed source used retrieval'))

    result=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-NO-PRIMARY','status':'COMPLETED'},question,{'items':[]})

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['citations']==[] and result['retrieved_count']==0


def test_question_api_loads_workflow_index_for_exact_workflow_metadata(
        client, project, monkeypatch):
    source='Discipline: Electrical'
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('metadata API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the discipline for Submittal 23-01?'})

    assert response.status_code==200
    result=response.json();assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']==source
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_metadata_parser_preserves_identity_role_and_complete_value():
    source=('RFI 42\nPriority: High\n'
            'RFI 43\nLocation: Level 2 Mechanical Room')

    assert _workflow_metadata_source_values(source)=={
        (('RFI','42'),'PRIORITY','high'),
        (('RFI','43'),'LOCATION','level 2 mechanical room')}
    assert _workflow_metadata_claim_values(
        'RFI 42 Priority: “High”.')=={(('RFI','42'),'PRIORITY','high')}


def test_answer_accepts_same_exact_workflow_metadata_field(client, project):
    source='Ball in Court: Project Architect'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='Who has the ball in court for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    grounded={'status':'ANSWERED','answer':'RFI 42 Ball in Court: “Project Architect”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    validate_answer_model(grounded,evidence,question)


@pytest.mark.parametrize(('source','answer'),[
    ('Priority: High','RFI 42 Priority: “Low”.'),
    ('Discipline: Mechanical','RFI 42 Location: “Mechanical”.'),
    ('Assigned To: Project Architect','RFI 42 Ball in Court: “Project Architect”.'),
    ('Email Priority: High','RFI 42 Email Priority: “High”.'),
])
def test_answer_rejects_inferred_role_swapped_or_email_workflow_metadata(
        client, project, source, answer):
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='What is the priority of RFI 42?'
    row=client.app.state.db.one(
        '''SELECT e.payload,d.name FROM evidence e JOIN documents d ON d.id=e.document_id
           WHERE e.run_id=?''',(run['id'],))
    item=json.loads(row['payload']);item['file_name']=row['name']
    item['locator']['section']='RFI 42 > RESPONSE';evidence=[item]
    wrong={'status':'ANSWERED','answer':answer,'source_findings':[],
           'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError):
        validate_answer_model(wrong,evidence,question)


def test_workflow_metadata_question_requires_explicit_scoped_field_and_no_conflict(
        client, project):
    source='Priority: High'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='What is the priority of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    evasive={'status':'ANSWERED','answer':'The item is marked high.','source_findings':[],
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':source}]}

    with pytest.raises(ValueError,match='requires an explicit scoped field'):
        validate_answer_model(evasive,evidence,question)

    conflict='Priority: High\nPriority: Urgent'
    conflict_run=_source_evidence(client,project,[('RFI-42-conflict.pdf',[conflict])])
    conflict_evidence=retrieve_evidence(client.app.state.db,conflict_run,question)
    conflict_evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    selected={'status':'ANSWERED','answer':'RFI 42 Priority: “High”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':conflict}]}
    with pytest.raises(ValueError,match='conflicting workflow metadata field values'):
        validate_answer_model(selected,conflict_evidence,question)


@pytest.mark.parametrize(('question','expected'),[
    ('What is the cost impact of RFI 42?',('RFI','42','COST_IMPACT')),
    ('What is the Potential Cost Impact field for Request for Information No. 0042?',
     ('RFI','42','COST_IMPACT')),
    ('Show me the schedule impact for Submittal 23-01.',
     ('SUBMITTAL','23-01','SCHEDULE_IMPACT')),
    ('What is the potential schedule impact in Submission MEP-023?',
     ('SUBMITTAL','MEP-023','SCHEDULE_IMPACT')),
    ('Will RFI 42 increase cost?',None),
    ('How much will RFI 42 cost?',None),
    ('What should the schedule impact be for RFI 42?',None),
    ('What is the cost impact of RFI 42 and RFI 43?',None),
])
def test_exact_workflow_impact_question_boundary(question,expected):
    assert requested_workflow_impact(question)==expected
    assert requires_workflow_impact_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source'),[
    ('RFI-42.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','What is the cost impact of RFI 42?',
     'Cost Impact: $4,500 allowance'),
    ('RFI-43.pdf','RFI_QUESTION',
     {'workflow_type':'RFI','identifier':'43','role':'QUESTION','status':'OPEN'},
     'RFI 43 > QUESTION','What is the potential cost impact for RFI 43?',
     'Potential Cost Impact: To be determined'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','What is the schedule impact for Submittal 23-01?',
     'Schedule Impact: No impact'),
    ('Submittal-MEP-023.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'MEP-023','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL MEP-023','What is the potential schedule impact for Submittal MEP-023?',
     'Potential Schedule Impact: 3 working days'),
])
def test_exact_workflow_impact_answers_from_explicit_field_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context, section,
        question, source):
    run=_source_evidence(client,project,[(file_name,[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
        lambda *_args,**_kwargs:pytest.fail('workflow impact used ordinary retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source
    assert source.split(': ',1)[1] in result['answer']
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_exact_workflow_impact_merges_alias_and_blocks_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['Cost Impact: No impact']),
        ('RFI-42-response.pdf',['Potential Cost Impact: no impact']),
        ('RFI-42-addendum.pdf',['Cost Impact: $5,000']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('impact conflict used retrieval'))

    merged=ProjectQuestions(db,object()).ask(
        run,'What is the cost impact of RFI 42?',build_workflow_index(contexts[:2]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'What is the cost impact of RFI 42?',build_workflow_index(contexts))

    assert merged['status']=='ANSWERED' and len(merged['citations'])==2
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in conflicted['answer']
    assert len(conflicted['citations'])==3


@pytest.mark.parametrize(('section','source'),[
    ('RFI 42 > RESPONSE','The change may delay the work by 3 days.'),
    ('RFI 42 > RESPONSE','Email Cost Impact: No impact'),
    ('EMAIL > HEADERS > RFI 42','Cost Impact: No impact'),
    ('EMAIL > QUOTED HISTORY > RFI 42','Cost Impact: No impact'),
    ('EMAIL > SIGNATURE > RFI 42','Cost Impact: No impact'),
    ('RFI 43 > RESPONSE','Cost Impact: No impact'),
])
def test_exact_workflow_impact_does_not_infer_or_cross_scope(
        client, project, monkeypatch, section, source):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42-response.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('unsupported impact used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What is the cost impact of RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE' and result['citations']==[]


def test_exact_workflow_impact_accepts_current_email_body(client, project, monkeypatch):
    source='Schedule Impact: 2 working days'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY > RFI 42'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('current Email impact used retrieval'))

    result=ProjectQuestions(db,object()).ask(run,'What is the schedule impact of RFI 42?',index)

    assert result['status']=='ANSWERED' and result['citations'][0]['quote']==source


def test_workflow_impact_caps_and_missing_primary_fail_closed(monkeypatch):
    primary_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    passage_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('impact primary-file cap read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('impact cap used retrieval'))

    missing=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-IMPACT-CAP','status':'COMPLETED'},
        'What is the cost impact of RFI 42?',{'items':[]})
    primary=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-IMPACT-CAP','status':'COMPLETED'},
        'What is the cost impact of RFI 42?',primary_index)
    passages=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-IMPACT-CAP','status':'COMPLETED'},
        'What is the cost impact of RFI 42?',passage_index)

    assert missing['status']=='INSUFFICIENT_EVIDENCE'
    assert 'does not contain one indexed primary source' in missing['answer']
    assert 'more than 32 primary files' in primary['answer']
    assert 'more than 32 candidate source passages' in passages['answer']


def test_question_api_loads_workflow_index_for_exact_workflow_impact(
        client, project, monkeypatch):
    source='Potential Schedule Impact: No impact'
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('impact API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the schedule impact for Submittal 23-01?'})

    assert response.status_code==200
    result=response.json();assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']==source
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_impact_parser_preserves_identity_role_and_complete_value():
    source=('RFI 42\nPotential Cost Impact: $4,500 allowance\n'
            'RFI 43\nSchedule Impact: 3 working days')

    assert _workflow_impact_source_values(source)=={
        (('RFI','42'),'COST_IMPACT','$4,500 allowance'),
        (('RFI','43'),'SCHEDULE_IMPACT','3 working days')}
    assert _workflow_impact_claim_values(
        'RFI 42 Cost Impact: “$4,500 allowance”.')=={
            (('RFI','42'),'COST_IMPACT','$4,500 allowance')}


def test_workflow_impact_validator_accepts_exact_and_rejects_unsupported_claims(
        client, project):
    source='Cost Impact: $4,500 allowance'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='What is the cost impact of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    citation=[{'evidence_id':'EV-SOURCE-1','quote':source}]
    grounded={'status':'ANSWERED','answer':'RFI 42 Cost Impact: “$4,500 allowance”.',
              'source_findings':[],'citations':citation}

    validate_answer_model(grounded,evidence,question)

    for answer,error in [
        ('RFI 42 Cost Impact: “$5,000”.','workflow impact field absent'),
        ('RFI 42 Schedule Impact: “$4,500 allowance”.','workflow impact field absent'),
        ('RFI 42 Email Cost Impact: “$4,500 allowance”.','requires an explicit scoped field'),
        ('The record includes an allowance.','requires an explicit scoped field'),
    ]:
        wrong={**grounded,'answer':answer}
        with pytest.raises(ValueError,match=error):
            validate_answer_model(wrong,evidence,question)


def test_workflow_impact_validator_rejects_cross_item_and_conflict(client, project):
    cross=('RFI 42\nCost Impact: No impact\n'
           'RFI 43\nCost Impact: $5,000')
    run=_source_evidence(client,project,[('RFI-log.pdf',[cross])])
    question='What is the cost impact of RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    borrowed={'status':'ANSWERED','answer':'RFI 42 Cost Impact: “$5,000”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':cross}]}
    with pytest.raises(ValueError,match='workflow impact field absent'):
        validate_answer_model(borrowed,evidence,question)

    conflict='Cost Impact: No impact\nPotential Cost Impact: $5,000'
    conflict_run=_source_evidence(client,project,[('RFI-42.pdf',[conflict])])
    conflict_evidence=retrieve_evidence(client.app.state.db,conflict_run,question)
    conflict_evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    selected={'status':'ANSWERED','answer':'RFI 42 Cost Impact: “No impact”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':conflict}]}
    with pytest.raises(ValueError,match='conflicting workflow impact field values'):
        validate_answer_model(selected,conflict_evidence,question)


def test_workflow_impact_source_finding_cannot_borrow_another_source_value(
        client, project):
    specification='Cost Impact: No impact'
    rfi='Cost Impact: $5,000 allowance'
    run=_source_evidence(client,project,[
        ('project-spec.txt',[specification]),('RFI-42.pdf',[rfi])])
    question='Compare the specification and RFI 42 cost impact.'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    for item in evidence:
        if item['file_name']=='RFI-42.pdf':item['locator']['section']='RFI 42 > RESPONSE'
    wrong={
        'status':'ANSWERED','answer':'The sources list different impact values.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification contains an impact field.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':specification}]},
            {'source_type':'RFI','file_name':'RFI-42.pdf',
             'statement':'RFI 42 Cost Impact: “No impact”.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':rfi}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains a workflow impact field'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('question','expected'),[
    ('What is the RFI Manager field for RFI 42?',('RFI','42','RFI Manager')),
    ('Show me the Category field for Request for Information No. 0042.',
     ('RFI','42','Category')),
    ('Tell me the Submittal Type field in Submittal 23-01.',
     ('SUBMITTAL','23-01','Submittal Type')),
    ('What is the Procurement Package field for Submission MEP-023?',
     ('SUBMITTAL','MEP-023','Procurement Package')),
    ('What is the RFI Manager for RFI 42?',None),
    ('What should the Category field be for RFI 42?',None),
    ('What is the Category field for RFI 42 and RFI 43?',None),
    ('What is the Status field for RFI 42?',None),
    ('What is the Cost Impact field for RFI 42?',None),
    ('What is the Email Category field for RFI 42?',None),
])
def test_exact_generic_workflow_field_question_boundary(question,expected):
    assert requested_workflow_field(question)==expected
    assert requires_workflow_field_index(question) is bool(expected)


@pytest.mark.parametrize(
    ('file_name','document_type','context','section','question','source','expected'),[
    ('RFI-42.pdf','RFI_RESPONSE',
     {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'},
     'RFI 42 > RESPONSE','What is the RFI Manager field for RFI 42?',
     'RFI Manager: Project Architect','Project Architect'),
    ('RFI-ARC-42.pdf','RFI_QUESTION',
     {'workflow_type':'RFI','identifier':'ARC-42','role':'QUESTION','status':'OPEN'},
     'RFI ARC-42 > QUESTION','What is the Category field for RFI ARC-42?',
     'Category : Design Coordination','Design Coordination'),
    ('Submittal-23-01.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL 23-01','What is the Submittal Type field for Submittal 23-01?',
     'Submittal Type: Product Data','Product Data'),
    ('Submittal-MEP-023.pdf','SUBMITTAL',
     {'workflow_type':'SUBMITTAL','identifier':'MEP-023','role':'SUBMITTAL','status':'PENDING'},
     'SUBMITTAL MEP-023','What is the Procurement Package field for Submittal MEP-023?',
     'Procurement Package: Mechanical Equipment','Mechanical Equipment'),
])
def test_exact_generic_workflow_field_answers_without_model_or_retrieval(
        client, project, tmp_path, monkeypatch, file_name, document_type, context, section,
        question, source, expected):
    run=_source_evidence(client,project,[(file_name,[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':file_name,
        'summary':{'document_type':document_type,'workflow_contexts':[context]}}])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('generic field used retrieval'))

    result=ProjectQuestions(db,gateway).ask(run,question,index)

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='LOCAL_PROJECT_EVIDENCE'
    assert result['citations'][0]['quote']==source and expected in result['answer']
    assert result['retrieved_count']==1 and requests==[]
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_generic_workflow_field_merges_formatting_and_blocks_conflicts(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('RFI-42-question.pdf',['RFI Manager: Project Architect']),
        ('RFI-42-response.pdf',['rfi   manager : project architect']),
        ('RFI-42-addendum.pdf',['RFI Manager: Construction Manager']),
    ]);db=client.app.state.db
    rows=db.all('''SELECT e.id,e.document_id,e.payload,d.name FROM evidence e
                   JOIN documents d ON d.id=e.document_id WHERE e.run_id=?''',(run['id'],))
    contexts=[]
    for row in rows:
        payload=json.loads(row['payload']);payload['locator']['section']='RFI 42 > RESPONSE'
        db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        contexts.append({'document_id':row['document_id'],'name':row['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}})
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('generic field conflict used retrieval'))

    merged=ProjectQuestions(db,object()).ask(
        run,'What is the RFI Manager field for RFI 42?',build_workflow_index(contexts[:2]))
    conflicted=ProjectQuestions(db,object()).ask(
        run,'What is the RFI Manager field for RFI 42?',build_workflow_index(contexts))

    assert merged['status']=='ANSWERED' and len(merged['citations'])==2
    assert conflicted['status']=='INSUFFICIENT_EVIDENCE'
    assert 'conflicting explicit values' in conflicted['answer']
    assert len(conflicted['citations'])==3


@pytest.mark.parametrize(('section','source'),[
    ('RFI 42 > RESPONSE','The project architect manages this RFI.'),
    ('RFI 42 > RESPONSE','Manager: Project Architect'),
    ('RFI 42 > RESPONSE','Email RFI Manager: Project Architect'),
    ('EMAIL > HEADERS > RFI 42','RFI Manager: Project Architect'),
    ('EMAIL > QUOTED HISTORY > RFI 42','RFI Manager: Project Architect'),
    ('EMAIL > SIGNATURE > RFI 42','RFI Manager: Project Architect'),
    ('RFI 43 > RESPONSE','RFI Manager: Project Architect'),
])
def test_generic_workflow_field_requires_exact_label_and_scope(
        client, project, monkeypatch, section, source):
    run=_source_evidence(client,project,[('RFI-42-response.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']=section
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'RFI-42-response.pdf',
        'summary':{'document_type':'RFI_RESPONSE','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('unsupported generic field used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the RFI Manager field for RFI 42?',index)

    assert result['status']=='INSUFFICIENT_EVIDENCE' and result['citations']==[]


def test_generic_workflow_field_accepts_current_email_body(client, project, monkeypatch):
    source='RFI Manager: Project Architect'
    run=_source_evidence(client,project,[('coordination.eml',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='EMAIL > BODY > RFI 42'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    index=build_workflow_index([{'document_id':row['document_id'],'name':'coordination.eml',
        'summary':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}}])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('current Email generic field used retrieval'))

    result=ProjectQuestions(db,object()).ask(
        run,'What is the RFI Manager field for RFI 42?',index)

    assert result['status']=='ANSWERED' and result['citations'][0]['quote']==source


def test_generic_workflow_field_caps_and_missing_primary_fail_closed(monkeypatch):
    primary_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':f'D-{number}','source':'PRIMARY'} for number in range(33)]}]}
    passage_index={'items':[{'kind':'RFI','identifier':'42','members':[
        {'document_id':'D-1','source':'PRIMARY'}]}]}
    class NoReadDatabase:
        def all(self,*_args,**_kwargs):pytest.fail('generic field primary cap read evidence')
    class FloodDatabase:
        def all(self,*_args,**_kwargs):return [{} for _ in range(33)]
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('generic field cap used retrieval'))

    missing=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-FIELD-CAP','status':'COMPLETED'},
        'What is the RFI Manager field for RFI 42?',{'items':[]})
    primary=ProjectQuestions(NoReadDatabase(),object()).ask(
        {'id':'RUN-FIELD-CAP','status':'COMPLETED'},
        'What is the RFI Manager field for RFI 42?',primary_index)
    passages=ProjectQuestions(FloodDatabase(),object()).ask(
        {'id':'RUN-FIELD-CAP','status':'COMPLETED'},
        'What is the RFI Manager field for RFI 42?',passage_index)

    assert missing['status']=='INSUFFICIENT_EVIDENCE'
    assert 'does not contain one indexed primary source' in missing['answer']
    assert 'more than 32 primary files' in primary['answer']
    assert 'more than 32 candidate source passages' in passages['answer']


def test_question_api_loads_workflow_index_for_generic_workflow_field(
        client, project, monkeypatch):
    source='Submittal Type: Shop Drawing'
    run=_source_evidence(client,project,[('Submittal-23-01.pdf',[source])]);db=client.app.state.db
    row=db.one('SELECT id,document_id,payload FROM evidence WHERE run_id=?',(run['id'],))
    payload=json.loads(row['payload']);payload['locator']['section']='SUBMITTAL 23-01'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    summary={'document_type':'SUBMITTAL','workflow_contexts':[{
        'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]}
    db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
               (run['id'],row['document_id'],'SUCCESS',json.dumps(summary)))
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('generic field API used retrieval'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],
        'question':'What is the Submittal Type field for Submittal 23-01?'})

    assert response.status_code==200
    result=response.json();assert result['status']=='ANSWERED'
    assert result['citations'][0]['quote']==source
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_generic_workflow_field_parser_preserves_identity_and_complete_value():
    source=('RFI 42\nRFI Manager: Project Architect\n'
            'RFI 43\nRFI Manager: Construction Manager')

    assert _workflow_field_source_values(source,'RFI Manager')=={
        (('RFI','42'),'project architect'),
        (('RFI','43'),'construction manager')}
    assert _workflow_field_claim_values(
        'RFI 42 RFI Manager: “Project Architect”.','RFI Manager')=={
            (('RFI','42'),'project architect')}


def test_generic_workflow_field_validator_accepts_exact_and_rejects_unsupported_claims(
        client, project):
    source='RFI Manager: Project Architect'
    run=_source_evidence(client,project,[('RFI-42.pdf',[source])])
    question='What is the RFI Manager field for RFI 42?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    citation=[{'evidence_id':'EV-SOURCE-1','quote':source}]
    grounded={'status':'ANSWERED','answer':'RFI 42 RFI Manager: “Project Architect”.',
              'source_findings':[],'citations':citation}

    validate_answer_model(grounded,evidence,question)

    for answer,error in [
        ('RFI 42 RFI Manager: “Construction Manager”.','workflow field absent'),
        ('RFI 43 RFI Manager: “Project Architect”.','workflow identifier absent'),
        ('RFI 42 Email RFI Manager: “Project Architect”.','requires an explicit scoped field'),
        ('RFI 42 Assistant RFI Manager: “Project Architect”.',
         'requires an explicit scoped field'),
        ('The manager is listed in the form.','requires an explicit scoped field'),
    ]:
        with pytest.raises(ValueError,match=error):
            validate_answer_model({**grounded,'answer':answer},evidence,question)


def test_generic_workflow_field_validator_rejects_conflict_and_cross_source_borrowing(
        client, project):
    question='What is the RFI Manager field for RFI 42?'
    conflict='RFI Manager: Project Architect\nRFI Manager: Construction Manager'
    run=_source_evidence(client,project,[('RFI-42.pdf',[conflict])])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    evidence[0]['locator']['section']='RFI 42 > RESPONSE'
    selected={'status':'ANSWERED','answer':'RFI 42 RFI Manager: “Project Architect”.',
              'source_findings':[],
              'citations':[{'evidence_id':'EV-SOURCE-1','quote':conflict}]}
    with pytest.raises(ValueError,match='conflicting workflow field values'):
        validate_answer_model(selected,evidence,question)

    specification='RFI Manager: Specification Coordinator'
    rfi='RFI Manager: Project Architect'
    comparison_run=_source_evidence(client,project,[
        ('project-spec.txt',[specification]),('RFI-42.pdf',[rfi])])
    comparison_question='Compare the specification and RFI 42 RFI Manager field.'
    comparison_evidence=retrieve_evidence(
        client.app.state.db,comparison_run,comparison_question)
    for item in comparison_evidence:
        if item['file_name']=='RFI-42.pdf':item['locator']['section']='RFI 42 > RESPONSE'
    specification_evidence=next(
        item for item in comparison_evidence if item['file_name']=='project-spec.txt')
    rfi_evidence=next(item for item in comparison_evidence if item['file_name']=='RFI-42.pdf')
    wrong={
        'status':'ANSWERED','answer':'The sources list different manager values.','citations':[],
        'source_findings':[
            {'source_type':_source_family(specification_evidence),'file_name':'project-spec.txt',
             'statement':'The specification contains a manager field.',
             'citations':[{'evidence_id':specification_evidence['evidence_id'],
                           'quote':specification}]},
            {'source_type':_source_family(rfi_evidence),'file_name':'RFI-42.pdf',
             'statement':'RFI 42 RFI Manager: “Specification Coordinator”.',
             'citations':[{'evidence_id':rfi_evidence['evidence_id'],'quote':rfi}]},
        ],
    }
    with pytest.raises(ValueError,match='source finding contains a workflow field'):
        validate_answer_model(wrong,comparison_evidence,question)
