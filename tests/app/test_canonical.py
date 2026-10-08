import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.canonical import CanonicalGraph,normalize_identifier
from app.db import Database
from app.questions import _canonical_identifier_rows,retrieve_evidence
from scripts.rebuild_canonical_v2 import rebuild
from .conftest import upload


def _canonical_fixture(client,project):
    document=upload(client,project['id'],'drawing-and-spec.pdf',b'synthetic source')
    run=client.app.state.runner.create(project['id'])
    db=client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    locator={**base['locator'],'page_number':7,'sheet':'A7.2',
             'section':'Section 03 30 00 > Part 2','paragraph':'2.3.6',
             'bbox':[10.0,20.0,300.0,80.0],'coordinate_system':'pdf-points'}
    rows=[]
    for index,text in enumerate((
            'Paragraph 2.3.6 requires a crystalline waterproofing admixture.',
            'Install the admixture at the manufacturer stated dosage.'),1):
        evidence={**base,'evidence_id':f'EV-CANON-{index}','tenant_id':'local',
                  'project_id':project['id'],'input_snapshot_id':run['snapshot_id'],
                  'document_id':document['document_id'],'raw_text':text,
                  'locator':{**locator,'bbox':[10.0,20.0+index*70,300.0,80.0+index*70]},
                  'extraction_method':'TEXT_LAYER','parser_version':'synthetic-1'}
        rows.append((run['id']+':'+evidence['evidence_id'],run['id'],project['id'],
                     document['document_id'],json.dumps(evidence),'EXTRACTED',None,''))
    visual={**base,'evidence_id':'EV-CANON-VISION','tenant_id':'local',
            'project_id':project['id'],'input_snapshot_id':run['snapshot_id'],
            'document_id':document['document_id'],'raw_text':'Invented visual waterproofing claim.',
            'locator':{**locator,'bbox':[0.0,0.0,600.0,800.0]},
            'content_basis':'MODEL_VISION_OUTPUT','extraction_method':'VISION',
            'parser_version':'synthetic-vision-1'}
    rows.append((run['id']+':'+visual['evidence_id'],run['id'],project['id'],
                 document['document_id'],json.dumps(visual),'EXTRACTED',None,''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',rows)
        connection.execute('INSERT INTO document_results VALUES(?,?,?,?)',
                           (run['id'],document['document_id'],'SUCCESS',json.dumps(
                               {'status':'SUCCESS','pages':[{'page':7,'status':'SUCCESS'}],
                                'warnings':[],'parser_version':'synthetic-1'})))
    return db,client.app.state.runner.get(run['id']),document


def test_canonical_graph_builds_hierarchy_identifiers_spans_and_indexes(client,project):
    db,run,_=_canonical_fixture(client,project)
    result=CanonicalGraph(db).rebuild_run(run)
    assert result['validation']['ok'] is True
    assert result['validation']['counts']['source_evidence_count']==3
    assert result['validation']['counts']['span_count']==3
    kinds={row['node_type'] for row in db.all(
        'SELECT node_type FROM content_nodes WHERE run_id=?',(run['id'],))}
    assert {'DOCUMENT','PAGE','SHEET','SECTION','PARAGRAPH','TEXT_BLOCK','VISUAL_CONTEXT'}<=kinds
    identifiers={(row['identifier_type'],row['normalized_value']) for row in db.all(
        'SELECT identifier_type,normalized_value FROM content_identifiers WHERE run_id=?',(run['id'],))}
    assert ('SHEET','A7.2') in identifiers
    assert ('PARAGRAPH','2.3.6') in identifiers
    assert ('SPEC_SECTION','03 30 00') in identifiers
    assert db.one('SELECT COUNT(*) AS n FROM content_search')['n']==2
    assert db.one('SELECT COUNT(*) AS n FROM content_bounds')['n']==3
    assert normalize_identifier('PARAGRAPH','Para. 2.3.6')=='2.3.6'


def test_canonical_build_is_idempotent_and_visual_text_is_not_searchable(client,project):
    db,run,_=_canonical_fixture(client,project);graph=CanonicalGraph(db)
    first=graph.rebuild_run(run)['validation']['counts']
    second=graph.rebuild_run(run)['validation']['counts']
    assert second==first
    assert retrieve_evidence(db,run,'invented visual observation')==[]
    evidence=retrieve_evidence(db,run,'What does Paragraph 2.3.6 require?')
    assert {item['evidence_id'] for item in evidence}=={'EV-CANON-1','EV-CANON-2'}


def test_canonical_graph_preserves_table_row_structure_without_forging_paragraph_identifier(
        client,project):
    db,run,_=_canonical_fixture(client,project)
    row=db.one("SELECT id,payload FROM evidence WHERE payload LIKE '%EV-CANON-1%'")
    evidence=json.loads(row['payload'])
    evidence['raw_text']='Structural | 13'
    evidence['locator']['paragraph']='Table 2 row 3'
    evidence['locator']['native_element_id']='page-7-table-2-row-3-part-1'
    evidence['text_map']=[{'start':0,'end':10,'bbox':[10,20,80,40]},
                          {'start':13,'end':15,'bbox':[100,20,120,40]}]
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(evidence),row['id']))

    CanonicalGraph(db).rebuild_run(run)

    table=db.one("""SELECT metadata FROM content_nodes
                    WHERE run_id=? AND json_extract(metadata,'$.structure_kind')='TABLE'""",
                 (run['id'],))
    table_row=db.one("""SELECT metadata FROM content_nodes
                        WHERE run_id=? AND json_extract(metadata,'$.structure_kind')='TABLE_ROW'
                          AND raw_text='Structural | 13'""",(run['id'],))
    assert json.loads(table['metadata'])['table_number']==2
    metadata=json.loads(table_row['metadata'])
    assert (metadata['table_number'],metadata['row_number'],metadata['cell_count'])==(2,3,2)
    assert db.one("""SELECT COUNT(*) AS n FROM content_identifiers
                     WHERE run_id=? AND identifier_type='PARAGRAPH'
                       AND normalized_value='TABLE 2 ROW 3'""",(run['id'],))['n']==0


def test_visual_only_identifier_cannot_route_source_text(client,project):
    db,run,_=_canonical_fixture(client,project)
    row=db.one("SELECT id,payload FROM evidence WHERE payload LIKE '%EV-CANON-VISION%'")
    visual=json.loads(row['payload'])
    visual['locator']['paragraph']='9.9.9'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(visual),row['id']))
    CanonicalGraph(db).rebuild_run(run)
    identifier=db.one('''SELECT is_source_text FROM content_identifiers
                         WHERE run_id=? AND identifier_type='PARAGRAPH' AND normalized_value='9.9.9' ''',
                      (run['id'],))
    assert identifier['is_source_text']==0
    assert _canonical_identifier_rows(db,run['id'],'What does Paragraph 9.9.9 require?')==[]


def test_canonical_exact_section_and_paragraph_scopes_are_intersected(client,project):
    db,run,_=_canonical_fixture(client,project)
    rows=db.all("SELECT id,payload FROM evidence WHERE payload LIKE '%EV-CANON-%' ORDER BY rowid")
    first=json.loads(rows[0]['payload']);first['locator']['section']='SECTION 01100 > PART 1'
    first['locator']['paragraph']='1.04';first['raw_text']='Building G first floor: 4 months.'
    second=json.loads(rows[1]['payload']);second['locator']['section']='SECTION 01200 > PART 1'
    second['locator']['paragraph']='1.04';second['raw_text']='Unrelated payment paragraph.'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(first),rows[0]['id']))
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(second),rows[1]['id']))
    CanonicalGraph(db).rebuild_run(run)

    found=_canonical_identifier_rows(
        db,run['id'],'Using Section 01100 paragraph 1.04, what is the construction time?')

    assert [row['evidence_row_id'] for row in found]==[rows[0]['id']]


def test_canonical_graph_rejects_cross_project_evidence(client,project):
    db,run,document=_canonical_fixture(client,project)
    row=db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid LIMIT 1',(run['id'],))
    payload=json.loads(row['payload']);payload['project_id']='P-WRONG'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    with pytest.raises(Exception,match='scope mismatch'):
        CanonicalGraph(db).rebuild_document(run,document['document_id'])
    assert db.one('SELECT COUNT(*) AS n FROM content_nodes WHERE run_id=?',(run['id'],))['n']==0


def test_failed_run_build_is_not_eligible_for_retrieval(client,project):
    db,run,_=_canonical_fixture(client,project)
    row=db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid LIMIT 1',(run['id'],))
    payload=json.loads(row['payload']);payload['document_id']='DOC-WRONG'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    with pytest.raises(Exception,match='scope mismatch'):
        CanonicalGraph(db).rebuild_run(run)
    assert db.one('SELECT state FROM canonical_builds WHERE run_id=?',(run['id'],))['state']=='FAILED'
    assert retrieve_evidence(db,run,'Paragraph 2.3.6')


def test_runner_builds_canonical_graph_before_extraction(client,project):
    upload(client,project['id'],'spec.txt',b'Section 03 30 00\nParagraph 2.3.6 requires curing.')
    run=client.app.state.runner.create(project['id'])
    client.app.state.runner.process(run['id'])
    build=client.app.state.db.one('SELECT state,node_count,span_count FROM canonical_builds WHERE run_id=?',
                                  (run['id'],))
    assert build['state']=='READY' and build['node_count']>0 and build['span_count']>0


def test_staging_rebuild_copies_source_read_only_and_validates(client,project,tmp_path):
    source_db,run,_=_canonical_fixture(client,project)
    source=source_db.path;output=tmp_path/'canonical-staging.sqlite3'
    assert source_db.one('SELECT COUNT(*) AS n FROM content_nodes')['n']==0
    result=rebuild(source,output)
    assert result['integrity_check']=='ok' and result['run_count']==1
    staged=Database(output)
    assert staged.one('SELECT state FROM canonical_builds WHERE run_id=?',(run['id'],))['state']=='READY'
    assert staged.one('SELECT COUNT(*) AS n FROM content_nodes WHERE run_id=?',(run['id'],))['n']>0
    assert Database(source).one('SELECT COUNT(*) AS n FROM content_nodes')['n']==0
    with pytest.raises(ValueError,match='different'):
        rebuild(source,source)


def test_staging_rebuild_script_runs_as_documented_cli(client,project,tmp_path):
    source_db,run,_=_canonical_fixture(client,project)
    output=tmp_path/'canonical-cli-staging.sqlite3'
    completed=subprocess.run(
        [sys.executable,'scripts/rebuild_canonical_v2.py','--source',str(source_db.path),
         '--output',str(output)],cwd=Path.cwd(),capture_output=True,text=True,check=False)
    assert completed.returncode==0,completed.stderr
    summary=json.loads(completed.stdout)
    assert summary['integrity_check']=='ok' and summary['run_count']==1
    assert Database(output).one('SELECT state FROM canonical_builds WHERE run_id=?',
                                (run['id'],))['state']=='READY'
