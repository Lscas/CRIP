import json
from pathlib import Path

from app.page_selector import select_pages
from .conftest import upload


def _raw_run(client,project,fragments,name='reference-spec.pdf'):
    document=upload(client,project['id'],name,b'synthetic reference source')
    runner=client.app.state.runner;db=client.app.state.db
    run=runner.create(project['id'],analysis_mode='REFERENCE_QA')
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    rows=[]
    for index,fragment in enumerate(fragments,1):
        evidence={
            **base,'evidence_id':f'EV-PAGE-{index}','tenant_id':'local',
            'project_id':project['id'],'input_snapshot_id':run['snapshot_id'],
            'document_id':document['document_id'],'raw_text':fragment['text'],
            'locator':{**base['locator'],'page_number':fragment.get('page',index),
                       'sheet':fragment.get('sheet'),
                       'section':fragment.get('section'),'paragraph':fragment.get('paragraph'),
                       'bbox':fragment.get('bbox',[0.0,0.0,100.0,100.0]),
                       'coordinate_system':'pdf-points'},
            'internal_revision_date':fragment.get('date','2025-01-01'),
            'revision_label':fragment.get('revision','1'),
            'extraction_method':fragment.get('method','TEXT_LAYER'),
            'content_basis':fragment.get('content_basis','SOURCE_TEXT'),
            'text_map':fragment.get('text_map',[]),
            'parser_version':'synthetic-page-selector',
        }
        rows.append((run['id']+':'+evidence['evidence_id'],run['id'],project['id'],
                     document['document_id'],json.dumps(evidence),'PENDING',None,''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',rows)
        connection.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
            run['id'],document['document_id'],'SUCCESS',json.dumps({
                'status':'SUCCESS','pages':[{'page':item.get('page',index),'status':'SUCCESS'}
                                            for index,item in enumerate(fragments,1)],
                'warnings':[],'parser_version':'synthetic-page-selector'})))
    return db,runner.get(run['id']),document


def _spatial_text(lines):
    text='';mapping=[]
    for top,words in lines:
        for token,left,right in words:
            if text:text+=' '
            start=len(text);text+=token
            mapping.append({'start':start,'end':len(text),'bbox':[left,top,right,top+10]})
    return text,mapping


def test_literal_page_selector_is_bounded_transparent_and_canonical_free(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':'General project administrative requirements.'},
        {'page':2,'section':'Section 07 14 00','paragraph':'2.3',
         'text':'Section 07 14 00 requires waterproofing dosage of 2 percent.'},
        {'page':3,'text':'Unrelated door hardware schedule.'},
    ])

    selection=select_pages(db,run,'What waterproofing dosage does Section 07 14 00 require?')

    assert selection.selected_pages and selection.selected_pages[0].page_number==2
    assert selection.selected_pages[0].matched_terms
    assert selection.selected_pages[0].issue_dates==('2025-01-01',)
    assert selection.selected_pages[0].revision_labels==('1',)
    assert selection.byte_count<=48_000 and len(selection.selected_pages)<=4
    assert [row['evidence_id'] for row in selection.evidence_rows]==['EV-PAGE-2']
    public=selection.public()
    assert public['semantic_graph_used'] is False and public['embeddings_used'] is False
    assert public['ranking_policy']=='LITERAL_SOURCE_PAGE_MATCHES_ONLY'
    assert public['selector_version']=='literal-page-selector-7'
    assert public['entity_scope_policy']=='NOT_APPLICABLE'
    assert db.one('SELECT 1 FROM canonical_builds WHERE run_id=?',(run['id'],),False) is None


def test_selector_v7_keeps_compact_limit_while_v3_through_v6_remain_replayable(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':index,'text':f'waterproofing matching source page {index}'}
        for index in range(1,8)
    ])

    current=select_pages(db,run,'waterproofing')
    compact=select_pages(
        db,run,'waterproofing',selector_version='literal-page-selector-4')
    scoped=select_pages(
        db,run,'waterproofing',selector_version='literal-page-selector-5')
    row_focus=select_pages(
        db,run,'waterproofing',selector_version='literal-page-selector-6')
    legacy=select_pages(
        db,run,'waterproofing',selector_version='literal-page-selector-3')

    assert current.selector_version=='literal-page-selector-7'
    assert compact.selector_version=='literal-page-selector-4'
    assert legacy.selector_version=='literal-page-selector-3'
    assert scoped.selector_version=='literal-page-selector-5'
    assert row_focus.selector_version=='literal-page-selector-6'
    assert len(current.selected_pages)==len(row_focus.selected_pages)==len(scoped.selected_pages)==len(compact.selected_pages)==4
    assert current.max_pages==compact.max_pages==4
    assert len(legacy.selected_pages)==6 and legacy.max_pages==6
    assert current.selected_pages==row_focus.selected_pages==scoped.selected_pages==compact.selected_pages==legacy.selected_pages[:4]
    assert len({current.selection_id,row_focus.selection_id,scoped.selection_id,
                compact.selection_id,legacy.selection_id})==5


def test_selector_v7_retains_v6_literal_word_forms_without_changing_v5_row_order(
        client,project):
    fragments=[
        {'page':24,'sheet':'A5.01','text':f'General door schedule distractor {index}.'}
        for index in range(45)
    ]+[
        {'page':24,'sheet':'A5.01',
         'text':'ALL DOORS TO BE 1 3/4" THICK, UNLESS NOTED OTHERWISE.'},
        {'page':24,'sheet':'A5.01','text':'ALL DOOR HANDLES TO BE LEVER TYPE.'},
    ]
    db,run,_=_raw_run(client,project,fragments)
    question=(
        'On sheet A5.01, what are the requirements for door thickness, '
        'door-handle type, and glazing stops?')

    current=select_pages(db,run,question)
    replay=select_pages(
        db,run,question,selector_version='literal-page-selector-5')

    assert {'thick','handle','handles','stop'}<=set(current.query_terms)
    assert any(row['raw_text'].startswith('ALL DOORS TO BE')
               for row in current.evidence_rows[:2])
    assert next(index for index,row in enumerate(replay.evidence_rows)
                if row['raw_text'].startswith('ALL DOORS TO BE'))>40
    assert 'thick' not in replay.query_terms and 'handle' not in replay.query_terms


def test_selector_v5_scopes_explicit_building_and_v4_replays_old_input(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':(
            'BUILDING G CODE ANALYSIS. TYPE V-B. OCCUPANCY E. ONE STORY. UNSPRINKLERED.')},
        {'page':2,'text':(
            'BUILDING I CODE ANALYSIS. TYPE V-A. OCCUPANCY E, B, S-1. '
            'TWO STORIES. FULLY SPRINKLERED.')},
        {'page':2,'text':'BUILDING G unrelated occupant-load subtotal 593.'},
        {'page':3,'text':'General code note applicable to existing school construction.'},
    ])

    scoped=select_pages(db,run,(
        'For Building I, what construction type, occupancy groups, story count, '
        'and sprinkler status are listed?'))
    replay=select_pages(db,run,(
        'For Building I, what construction type, occupancy groups, story count, '
        'and sprinkler status are listed?'),selector_version='literal-page-selector-4')

    public=scoped.public()
    assert len(scoped.query_terms)<=24
    assert scoped.selected_pages[0].page_number==2
    assert 'QUESTION_ENTITY_MATCH' in scoped.selected_pages[0].reason_codes
    assert public['question_entities']==['BUILDING I']
    assert public['excluded_by_entity_scope']=={'pages':1,'evidence_rows':1}
    assert 'BUILDING G unrelated' not in '\n'.join(
        row['raw_text'] for row in scoped.evidence_rows)
    assert any('BUILDING G CODE ANALYSIS' in row['raw_text'] for row in replay.evidence_rows)

    supplemental=select_pages(
        db,run,'construction type and sprinkler status',scope_question=(
            'For Building I, what construction type, occupancy groups, story count, '
            'and sprinkler status are listed?'))
    assert supplemental.public()['question_entities']==['BUILDING I']
    assert all('BUILDING G CODE ANALYSIS' not in row['raw_text']
               for row in supplemental.evidence_rows)


def test_selector_v5_adds_exact_spatial_lines_without_changing_v4_source(client,project):
    wall_text,wall_map=_spatial_text([
        (10,[('PAINT',10,35),('ON',40,55),('CMU',60,82),('1',180,186),
             ('3',192,198),('5/8"',202,230),('METAL',235,270),('STUD',275,305),
             ('@',310,318),('16"',323,345),('O.C.',350,375)]),
        (30,[('2',180,186),('6"',202,218),('METAL',235,270),('STUD',275,305),
             ('@',310,318),('16"',323,345),('O.C.',350,375)]),
    ])
    load_text,load_map=_spatial_text([
        (10,[('BUILDING',10,65),('G',70,78),('L1',180,194),('511',300,320)]),
        (30,[('L2',180,194),('593',300,320)]),
        (50,[('TOTAL',180,220),('1,103',300,330)]),
    ])
    db,run,_=_raw_run(client,project,[
        {'page':12,'sheet':'A3.00','text':wall_text,'text_map':wall_map},
        {'page':3,'sheet':'G2.10','text':load_text,'text_map':load_map},
    ])

    walls=select_pages(
        db,run,'On Sheet A3.00, identify wall substrate codes 1 and 2.',
        selector_version='literal-page-selector-5')
    loads=select_pages(
        db,run,'On Sheet G2.10, add Building G L1 and L2 occupant loads.',
        selector_version='literal-page-selector-5')
    replay=select_pages(
        db,run,'On Sheet A3.00, identify wall substrate codes 1 and 2.',
        selector_version='literal-page-selector-4')

    wall_layout=walls.evidence_rows[0]['layout_lines']
    assert 'PAINT ON CMU || 1 3 5/8" METAL STUD @ 16" O.C.' in wall_layout
    assert '2 6" METAL STUD @ 16" O.C.' in wall_layout
    assert loads.evidence_rows[0]['layout_lines'].splitlines()==[
        'BUILDING G || L1 || 511','L2 || 593','TOTAL || 1,103']
    assert walls.evidence_rows[0]['raw_text']==wall_text
    assert 'layout_lines' not in replay.evidence_rows[0]


def test_selector_v7_groups_exact_tokens_by_page_columns_across_source_blocks(
        client,project):
    attachment_text,attachment_map=_spatial_text([
        (10,[('(3)',315,330),('#10',334,355),('THRU',359,393),('MAX.',397,427),
             ('(2)',431,446),('LYR.',450,478)]),
        (30,[('GWB',315,346),('@',350,362),('EA.',366,387),('ZEE',391,415)]),
        (50,[('TOP',315,342),('TRACK',346,388),('TO',392,409),('MATCH',413,457)]),
        (70,[('STUD',315,349),('W/',353,369),('#10',373,394),('EA.',398,418),
             ('LEG',422,447)]),
    ])
    sealant_text,sealant_map=_spatial_text([
        (50,[('ACOUSTIC',700,765),('SEALANT',769,827),('@',831,843),
             ('EACH',847,882)]),
    ])
    side_text,side_map=_spatial_text([
        (70,[('SIDE,',700,733),('TYP.',737,766)]),
    ])
    db,run,_=_raw_run(client,project,[
        {'page':69,'text':attachment_text,'text_map':attachment_map},
        {'page':69,'text':sealant_text,'text_map':sealant_map},
        {'page':69,'text':side_text,'text_map':side_map},
    ])

    current=select_pages(
        db,run,'How is the top track attached, and where is acoustic sealant required?')
    replay=select_pages(
        db,run,'How is the top track attached, and where is acoustic sealant required?',
        selector_version='literal-page-selector-6')

    layout=current.evidence_rows[0]['layout_lines']
    assert ('(3) #10 THRU MAX. (2) LYR.\nGWB @ EA. ZEE\nTOP TRACK TO MATCH\n'
            'STUD W/ #10 EA. LEG\n||\nACOUSTIC SEALANT @ EACH\nSIDE, TYP.') in layout
    assert all('layout_lines' not in row for row in replay.evidence_rows)


def test_explicit_sheet_locator_outranks_dense_generic_pages(client,project):
    dense=[{'page':1,'text':'General finish notes require door frame paint.'}
           for _ in range(20)]
    db,run,_=_raw_run(client,project,[*dense,
        {'page':39,'sheet':'A3.40','text':'The requested finish note is on this sheet.'},
        {'page':40,'sheet':'A3.4','text':'A different sheet with a shorter identifier.'},
    ])

    selection=select_pages(
        db,run,'On sheet A3.40, what do the general finish notes require for door frame paint?')

    assert selection.query_terms[:2]==('sheet a3.40','a3.40')
    assert selection.selected_pages[0].page_number==39
    assert 'EXACT_IDENTIFIER_LOCATOR' in selection.selected_pages[0].reason_codes
    assert selection.selected_pages[0].score>selection.selected_pages[1].score


def test_ordinary_sheet_phrases_are_not_promoted_to_identifiers(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':'Volume I cover sheet discipline counts.'},
        {'page':2,'sheet':'A1.00','text':'Drawing sheets and notes.'},
    ])

    selection=select_pages(
        db,run,'Using discipline sheet counts on the drawing sheets, what is the total?')

    assert 'sheet counts' not in selection.query_terms
    assert 'drawing sheets' not in selection.query_terms
    assert not any('EXACT_IDENTIFIER_LOCATOR' in page.reason_codes
                   for page in selection.selected_pages)


def test_multi_field_project_identity_question_routes_drawing_cover_only(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':'Example School Renovation, 10 Main Street. Modernization build-out.'},
        {'page':2,'text':(
            'Project name DOE job number project address scope work. '
            'Project name DOE job number project address scope work.')},
    ],name='Example Drawings Volume 1.pdf')

    selection=select_pages(db,run,(
        'What are the project name, DOE job number, project address, and stated scope of work?'),
        max_pages=1)

    assert selection.selected_pages[0].page_number==1
    assert 'DRAWING_COVER_INTENT' in selection.selected_pages[0].reason_codes


def test_single_generic_field_does_not_trigger_drawing_cover_route(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':'Cover page without the requested detail.'},
        {'page':2,'text':'The equipment is located at the project address.'},
    ],name='Example Drawings Volume 1.pdf')

    selection=select_pages(db,run,'What is the project address?',max_pages=1)

    assert selection.selected_pages[0].page_number==2
    assert 'DRAWING_COVER_INTENT' not in selection.selected_pages[0].reason_codes


def test_page_selection_preview_and_v3_preview_work_without_canonical_graph(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':4,'text':'The approved coating color is blue. Finish key PT9 applies.'},
    ])
    payload={'run_id':run['id'],'question':'What approved coating color applies to Finish key PT9?'}

    selected=client.post(f"/api/projects/{project['id']}/page-selections/preview",json=payload)
    v3=client.post(f"/api/projects/{project['id']}/questions-v3/preview",json=payload)

    assert selected.status_code==200,selected.text
    assert selected.json()['pages'][0]['page_number']==4
    assert v3.status_code==200,v3.text
    assert v3.json()['page_selection']['selection_id']==selected.json()['selection_id']
    assert db.one('SELECT 1 FROM canonical_builds WHERE run_id=?',(run['id'],),False) is None


def test_selector_excludes_model_vision_narration_and_preserves_exact_prefix(client,project):
    db,run,_=_raw_run(client,project,[
        {'page':1,'text':'waterproofing '+('x'*3000)},
        {'page':2,'text':'waterproofing invented model observation',
         'method':'VISION','content_basis':'MODEL_VISION_OUTPUT'},
    ])

    selection=select_pages(db,run,'waterproofing',max_pages=2,max_bytes=1000)

    assert len(selection.evidence_rows)==1
    assert selection.evidence_rows[0]['evidence_id']=='EV-PAGE-1'
    assert len(selection.evidence_rows[0]['raw_text'].encode('utf-8'))==1000
    assert selection.evidence_rows[0]['raw_text'].startswith('waterproofing ')
    assert selection.byte_count==1000


def test_reference_run_stops_after_local_parse_without_model_or_canonical_work(
        client,project,monkeypatch):
    upload(client,project['id'],'reference-note.txt',b'PT9 approved color is blue.')
    runner=client.app.state.runner
    run=runner.create(project['id'],analysis_mode='REFERENCE_QA')
    monkeypatch.setattr(runner.gateway,'extract',lambda *_args,**_kwargs: (_ for _ in ()).throw(
        AssertionError('reference preparation called the model')))
    monkeypatch.setattr(runner.gateway,'extract_many',lambda *_args,**_kwargs: (_ for _ in ()).throw(
        AssertionError('reference preparation called the model')))

    runner.process(run['id'])

    current=runner.get(run['id']);db=client.app.state.db
    assert current['status']=='PARTIAL' and current['stage']=='Local page catalog ready'
    assert current['capabilities']['analysis_mode']=='REFERENCE_QA'
    assert db.one('SELECT 1 FROM canonical_builds WHERE run_id=?',(run['id'],),False) is None
    assert db.one('SELECT 1 FROM model_calls WHERE run_id=?',(run['id'],),False) is None
    assert db.one('SELECT 1 FROM records WHERE run_id=?',(run['id'],),False) is None
    assert db.one('SELECT 1 FROM evidence WHERE run_id=?',(run['id'],),False) is not None


def test_reference_mode_is_opt_in_and_legacy_analysis_remains_default(client,project):
    upload(client,project['id'],'mode-note.txt',b'Local mode check.')

    legacy=client.post(f"/api/projects/{project['id']}/analysis-runs",json={'local_workers':1})
    assert legacy.status_code==202,legacy.text
    assert legacy.json()['capabilities']['analysis_mode']=='LEGACY_ANALYSIS'
    client.post(f"/api/analysis-runs/{legacy.json()['id']}/cancel").raise_for_status()

    reference=client.post(f"/api/projects/{project['id']}/analysis-runs",json={
        'local_workers':1,'analysis_mode':'REFERENCE_QA'})
    assert reference.status_code==202,reference.text
    assert reference.json()['capabilities']['analysis_mode']=='REFERENCE_QA'
