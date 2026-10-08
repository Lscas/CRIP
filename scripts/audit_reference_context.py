"""Read-only v7/v8 source-transport audit; no prompts, source text or model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.db import dumps
from app.evidence_loop import INITIAL_CONTEXT_BYTES,_fit_rows
from app.gateway import _evidence_source_groups
from app.page_selector import COMPLETE_SELECTOR_VERSION,SELECTOR_VERSION,select_pages


class ReadSnapshot:
    """One read transaction, including committed WAL data; never run migrations."""
    def __init__(self,path:Path):
        self.connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        self.connection.execute('BEGIN')

    def all(self,query,args=()):
        return [dict(row) for row in self.connection.execute(query,args).fetchall()]

    def one(self,query,args=(),required=True):
        row=self.connection.execute(query,args).fetchone()
        if row is None and required:raise ValueError('Requested saved source was not found.')
        return dict(row) if row else None

    def close(self):self.connection.close()


def audit(db,run:dict,questions:list[dict])->dict:
    records=[]
    for item in questions:
        modes={}
        for selector in (SELECTOR_VERSION,COMPLETE_SELECTOR_VERSION):
            selection=select_pages(db,run,item['question'],selector_version=selector)
            rows=list(selection.evidence_rows)
            sent=_fit_rows(rows,None if selector==COMPLETE_SELECTOR_VERSION else INITIAL_CONTEXT_BYTES)
            groups=_evidence_source_groups(sent)
            group_refs=[ref for group in groups for ref in group['ordered_evidence_refs']]
            sent_ids={row['evidence_id'] for row in sent}
            manifest=[{'evidence_id':row['evidence_id'],
                       'source_sha256':hashlib.sha256(row['raw_text'].encode()).hexdigest(),
                       'input_sha256':hashlib.sha256(row.get('prompt_text',row['raw_text']).encode()).hexdigest(),
                       'layout_sha256':hashlib.sha256(row.get('layout_lines','').encode()).hexdigest()}
                      for row in sent]
            modes[selector]={
                'selection_id':selection.selection_id,
                'selected_pages':[{'document_id':page.document_id,'page_number':page.page_number}
                                  for page in selection.selected_pages],
                'selected_rows':len(rows),'input_rows':len(sent),
                'selected_rows_omitted_from_input':sum(row['evidence_id'] not in sent_ids for row in rows),
                'input_rows_clipped':sum(row.get('prompt_text',row['raw_text'])!=row['raw_text'] for row in sent),
                'source_bytes':sum(len(row['raw_text'].encode()) for row in sent),
                'input_source_bytes':sum(len(row.get('prompt_text',row['raw_text']).encode()) for row in sent),
                'layout_bytes':sum(len(row.get('layout_lines','').encode()) for row in sent),
                'layout_pages':sum(bool(row.get('layout_lines')) for row in sent),
                'relation_groups':len(groups),
                'every_alias_grouped_once':sorted(group_refs)==sorted(f'E{i}' for i in range(1,len(sent)+1)),
                'input_manifest_sha256':hashlib.sha256(dumps(manifest).encode()).hexdigest(),
                'source_conflict_count':len(selection.source_conflicts),
                'excluded_by_entity_scope':selection.public()['excluded_by_entity_scope'],
            }
        records.append({'question_id':item['id'],
                        'question_sha256':hashlib.sha256(item['question'].encode()).hexdigest(),
                        'selectors':modes})
    return {'report_version':'reference-source-transport-audit-1',
            'run_id':run['id'],'project_id':run['project_id'],'snapshot_id':run['snapshot_id'],
            'question_count':len(records),'model_calls':0,'source_text_in_report':False,
            'scope':'initial selected-source transport only; not retrieval completeness, semantic relationships or answer correctness',
            'records':records}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve() in (args.database.resolve(),args.questions.resolve()):
        parser.error('The audit output must not overwrite its inputs.')
    fixture=json.loads(args.questions.read_text(encoding='utf-8'))
    db=ReadSnapshot(args.database)
    try:
        run=db.one('SELECT * FROM runs WHERE id=?',(fixture['frozen_context']['run_id'],))
        if any(run[key]!=fixture['frozen_context'][key] for key in ('project_id','snapshot_id')):
            raise ValueError('The saved run differs from the frozen fixture.')
        result=audit(db,run,fixture['questions'])
    finally:db.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={version:{key:sum(row['selectors'][version][key] for row in result['records'])
                      for key in ('input_rows','selected_rows_omitted_from_input','input_rows_clipped',
                                  'input_source_bytes','layout_bytes')}
             for version in (SELECTOR_VERSION,COMPLETE_SELECTOR_VERSION)}
    print(json.dumps({'question_count':result['question_count'],'selectors':summary,'model_calls':0}))
    return 0


if __name__=='__main__':raise SystemExit(main())
