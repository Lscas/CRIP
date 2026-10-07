"""Read-only retrieval coverage for the frozen Kapolei question set.

The report contains only counts and names of locked expected facts.  It never
serializes source evidence, citations, prompts, provider responses or secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.questions import retrieve_evidence


EXPECTATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    'S1': (
        ('project_name', r'kapolei high school'),
        ('doe_job', r'q82227[ -]21'),
        ('address', r'91[ -]5007'),
        ('modernization_scope', r'modernization|new finishes.{0,80}(?:fixtures|equipment)'),
    ),
    'S2': (
        ('building_i_first_floor_4_months', r'building i.{0,120}first floor.{0,120}4 months'),
        ('building_g_first_floor_4_months', r'building g.{0,120}first floor.{0,120}4 months'),
        ('building_g_second_floor_4_months', r'building g.{0,120}second floor.{0,120}4 months'),
        ('one_floor_constraint', r'one floor.{0,120}vacat|only one floor'),
    ),
    'S3': (
        ('building_g', r'building g'), ('type_va', r'type.{0,40}v[ -]?a'),
        ('occupancy_s1', r'(?:occupancy|group).{0,100}s[ -]?1'),
        ('two_stories', r'(?:stor(?:y|ies)|story/heights).{0,40}(?:2|two)'),
        ('sprinklered', r'fully sprinklered|automatic fire sprinkler'),
    ),
    'S4': (
        ('building_i', r'building i'), ('type_va', r'type.{0,40}v[ -]?a'),
        ('occupancy_s1', r'(?:occupancy|group).{0,100}s[ -]?1'),
        ('two_stories', r'(?:stor(?:y|ies)|story/heights).{0,40}(?:2|two)'),
        ('sprinklered', r'fully sprinklered|automatic fire sprinkler'),
    ),
    'S5': (
        ('general_7', r'general.{0,80}\b7\b'),
        ('architectural_48', r'architectural.{0,80}\b48\b'),
        ('structural_13', r'structural.{0,80}\b13\b'),
        ('mechanical_20', r'mechanical.{0,80}\b20\b'),
        ('fire_protection_8', r'fire protection.{0,80}\b8\b'),
        ('plumbing_19', r'plumbing.{0,80}\b19\b'),
    ),
    'D1': (
        ('code_1_stud', r'(?:code\s*)?1.{0,160}3[ -]?5/8.{0,160}(?:metal )?stud'),
        ('code_2_stud', r'(?:code\s*)?2.{0,160}\b6(?: inch|\").{0,160}(?:metal )?stud'),
        ('code_21_cmu', r'21.{0,160}8(?: inch|\").{0,160}cmu.{0,100}(?:solid|grout)'),
        ('code_22_existing_cmu', r'22.{0,160}existing.{0,120}8(?: inch|\").{0,120}cmu'),
        ('default_16_oc', r'(?:interior|stud framing|furring).{0,240}16(?: inch|\").{0,80}(?:o\.?c\.?|on center)'),
    ),
    'D2': (
        ('door_thickness', r'1[ -]?3/4(?: inch|\")'),
        ('lever_handle', r'lever.{0,80}(?:handle|type)|(?:handle|type).{0,80}lever'),
        ('ada', r'ada'),
        ('corridor_side', r'corridor side'),
        ('relite_stops', r'relite.{0,120}(?:glazing|stop)'),
    ),
    'D3': (
        ('floor_transition', r'(?:flooring|color).{0,160}transition.{0,160}(?:center|door)'),
        ('frame_pt2', r'(?:door|relite).{0,160}frame.{0,160}pt\s*2'),
        ('architect_approval', r'(?:color|colour).{0,120}approv.{0,120}architect|architect.{0,120}approv'),
        ('ceiling_pt1', r'gypsum.{0,160}ceiling.{0,160}pt\s*1'),
    ),
    'D4': (
        ('one_screw_each_leg', r'one.{0,40}#?10 screw.{0,120}each leg'),
        ('three_screws_each_zee', r'three.{0,40}#?10 screw.{0,160}each zee'),
        ('two_gwb_layers', r'(?:no more than|maximum).{0,80}two.{0,100}(?:gwb|gypsum)'),
        ('ul_p516', r'ul\s*p\s*516'),
        ('acoustic_sealant', r'acoustic sealant'),
        ('section_07900', r'(?:section\s*)?07900'),
    ),
    'D5': (
        ('rectangular_4_ft', r'rectangular duct.{0,180}4[ -]?(?:foot|feet|ft)'),
        ('three_eighth_rods', r'3/8(?: inch|\").{0,100}(?:hanger )?rod'),
        ('two_piece_hanger', r'1(?: inch|\")?\s*(?:x|by)\s*1/8.{0,120}(?:two[ -]piece|hanger)'),
        ('concealed_straps', r'(?:duct )?strap.{0,180}concealed'),
    ),
    'C1': (
        ('general_7', r'general.{0,80}\b7\b'),
        ('architectural_48', r'architectural.{0,80}\b48\b'),
        ('structural_13', r'structural.{0,80}\b13\b'),
        ('mechanical_20', r'mechanical.{0,80}\b20\b'),
        ('fire_protection_8', r'fire protection.{0,80}\b8\b'),
        ('plumbing_19', r'plumbing.{0,80}\b19\b'),
    ),
    'C2': (
        ('l1_511', r'(?:l1|level 1).{0,160}\b511\b|\b511\b.{0,160}(?:l1|level 1)'),
        ('l2_593', r'(?:l2|level 2).{0,160}\b593\b|\b593\b.{0,160}(?:l2|level 2)'),
        ('printed_total_1103', r'\b1?103\b'),
    ),
    'C3': (
        ('building_i_first_floor_4_months', r'building i.{0,120}first floor.{0,120}4 months'),
        ('building_g_first_floor_4_months', r'building g.{0,120}first floor.{0,120}4 months'),
        ('building_g_second_floor_4_months', r'building g.{0,120}second floor.{0,120}4 months'),
        ('one_floor_constraint', r'one floor.{0,120}vacat|only one floor'),
    ),
    'C4': (
        ('contract_589_days', r'\b589\b.{0,100}(?:calendar )?days|(?:calendar )?days.{0,100}\b589\b'),
        ('onsite_367_days', r'\b367\b.{0,100}(?:calendar )?days|(?:calendar )?days.{0,100}\b367\b'),
    ),
    'C5': (
        ('liquidated_2000', r'\$?\s*2,?000(?:\.00)?'),
        ('punch_list_10_percent', r'punch.{0,160}10\s*(?:%|percent)'),
        ('closing_5_percent', r'closing.{0,160}5\s*(?:%|percent)'),
    ),
}


class ReadOnlyDatabase:
    """The minimal Database protocol used by question retrieval, opened immutable."""

    def __init__(self,path: Path):
        self.path=path.resolve()
        if not self.path.is_file():raise FileNotFoundError(self.path)
        self._uri='file:'+self.path.as_posix()+'?mode=ro&immutable=1'

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self._uri,uri=True)
        connection.row_factory=sqlite3.Row
        try:yield connection
        finally:connection.close()

    def one(self,query: str,args=(),required: bool=True):
        with self.connect() as connection:row=connection.execute(query,args).fetchone()
        if row is None and required:raise LookupError('row not found')
        return dict(row) if row else None

    def all(self,query: str,args=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query,args).fetchall()]


def _normalized_corpus(evidence: list[dict]) -> str:
    parts=[]
    for item in evidence:
        text=item.get('raw_text')
        if isinstance(text,str):parts.append(text)
        locator=item.get('locator')
        if isinstance(locator,dict):
            parts.extend(str(locator.get(key) or '') for key in ('section','paragraph','sheet'))
    corpus='\n'.join(parts).casefold().translate(str.maketrans({
        '\u2010':'-','\u2011':'-','\u2012':'-','\u2013':'-','\u2014':'-',
        '\u201c':'"','\u201d':'"','\u2033':'"','\u00a0':' ',
    }))
    return re.sub(r'\s+',' ',corpus)


def score_question(question_id: str,evidence: list[dict]) -> dict:
    checks=EXPECTATIONS.get(question_id)
    if checks is None:raise ValueError(f'No locked expectation for {question_id}')
    corpus=_normalized_corpus(evidence)
    found=[label for label,pattern in checks if re.search(pattern,corpus,re.I)]
    missed=[label for label,_ in checks if label not in found]
    return {'retrieved_count':len(evidence),'atoms_found':len(found),'atoms_total':len(checks),
            'complete':not missed,'found':found,'missed':missed}


def benchmark_database(path: Path,questions: list[dict],run_id: str,label: str) -> dict:
    database=ReadOnlyDatabase(path);before=path.stat();started=time.perf_counter()
    run=database.one('SELECT id,status FROM runs WHERE id=?',(run_id,))
    model_calls_before=database.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    rows=[]
    for item in questions:
        evidence=retrieve_evidence(database,run,item['question'])
        rows.append({'id':item['id'],**score_question(item['id'],evidence)})
    model_calls_after=database.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise RuntimeError('benchmark database changed during read-only measurement')
    builds=database.all('''SELECT run_id,state,builder_version,node_count,identifier_count
                            FROM canonical_builds ORDER BY run_id''')
    return {
        'label':label,'database_file':path.name,'run_id':run_id,
        'elapsed_ms':round((time.perf_counter()-started)*1000,3),
        'evidence_count':database.one('SELECT COUNT(*) AS n FROM evidence')['n'],
        'model_calls_before':model_calls_before,'model_calls_after':model_calls_after,
        'paid_calls_made':0,'canonical_builds':builds,
        'questions_complete':sum(row['complete'] for row in rows),
        'questions_total':len(rows),
        'atoms_found':sum(row['atoms_found'] for row in rows),
        'atoms_total':sum(row['atoms_total'] for row in rows),
        'questions':rows,
    }


def compare(baseline: dict,candidate: dict) -> dict:
    baseline_rows={row['id']:row for row in baseline['questions']}
    candidate_rows={row['id']:row for row in candidate['questions']}
    changes=[]
    for question_id in baseline_rows:
        old=baseline_rows[question_id];new=candidate_rows[question_id]
        changes.append({
            'id':question_id,
            'atom_delta':new['atoms_found']-old['atoms_found'],
            'newly_found':sorted(set(new['found'])-set(old['found'])),
            'regressed':sorted(set(old['found'])-set(new['found'])),
        })
    return {
        'version':'1.0','scope':'retrieval_only','fixed_question_count':len(changes),
        'raw_evidence_in_report':False,'provider_calls_made':0,
        'baseline':baseline,'candidate':candidate,
        'aggregate_delta':{
            'questions_complete':candidate['questions_complete']-baseline['questions_complete'],
            'atoms_found':candidate['atoms_found']-baseline['atoms_found'],
        },
        'changes':changes,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-db',type=Path,required=True)
    parser.add_argument('--candidate-db',type=Path,required=True)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-run-id')
    parser.add_argument('--candidate-run-id')
    args=parser.parse_args()
    if args.baseline_db.resolve()==args.candidate_db.resolve():
        parser.error('baseline and candidate databases must differ')
    payload=json.loads(args.questions.read_text(encoding='utf-8'))
    questions=payload.get('questions');run_id=payload.get('live_analysis_run_id')
    if not isinstance(questions,list) or not questions or not isinstance(run_id,str):
        parser.error('question file is malformed')
    ids=[item.get('id') for item in questions]
    if ids!=list(EXPECTATIONS):parser.error('question IDs do not match the locked benchmark')
    baseline=benchmark_database(
        args.baseline_db,questions,args.baseline_run_id or run_id,'baseline')
    candidate=benchmark_database(
        args.candidate_db,questions,args.candidate_run_id or run_id,'candidate')
    report=compare(baseline,candidate)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({
        'output':str(args.output),'baseline_atoms':baseline['atoms_found'],
        'candidate_atoms':candidate['atoms_found'],
        'atom_delta':report['aggregate_delta']['atoms_found'],
        'baseline_complete':baseline['questions_complete'],
        'candidate_complete':candidate['questions_complete'],
        'provider_calls_made':0,'raw_evidence_in_report':False,
    },ensure_ascii=False,separators=(',',':')))
    return 0


if __name__=='__main__':raise SystemExit(main())
