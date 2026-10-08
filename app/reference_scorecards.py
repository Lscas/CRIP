"""Human-adjudicated, zero-call scorecards for frozen Reference evaluations."""
from __future__ import annotations

import hashlib
import json

from app.db import Database,DomainError,dumps,now,uid
from app.reference_evaluations import ReferenceEvaluationStore
from contracts.runtime_rules import validate_schema

MAX_SCORECARD_BYTES=4*1024*1024
FIXED_GATE_QUESTIONS=15
CONTRACT_VALID_REQUIRED=15
FULLY_USABLE_REQUIRED=12
VERDICTS=('FULLY_USABLE','PARTIAL','UNUSABLE')


class ReferenceScorecardStore:
    def __init__(self,db:Database,evaluations:ReferenceEvaluationStore):
        self.db=db;self.evaluations=evaluations

    @staticmethod
    def _empty_adjudication()->dict:
        return {
            'verdict':'UNREVIEWED','unsupported_claim':False,'note':'',
            'version':0,'event_id':None,'updated_at':None,
        }

    @staticmethod
    def _adjudication(row:dict|None)->dict:
        if row is None:return ReferenceScorecardStore._empty_adjudication()
        return {
            'verdict':row['verdict'],'unsupported_claim':bool(row['unsupported_claim']),
            'note':row['note'],'version':row['version'],'event_id':row['event_id'],
            'updated_at':row['updated_at'],
        }

    def scorecard(self,evaluation_id:str)->dict:
        evaluation=self.evaluations.get(evaluation_id)
        rows={row['item_id']:row for row in self.db.all(
            '''SELECT item_id,verdict,unsupported_claim,note,version,event_id,updated_at
               FROM reference_evaluation_adjudications WHERE evaluation_id=?''',
            (evaluation_id,))}
        profile=evaluation['profile']
        named_profile='profile_id' in profile
        authenticated_results=set()
        for source_item in evaluation['items']:
            if source_item['result'] is not None:
                self.evaluations.authenticate_item_result(
                    evaluation,source_item,source_item['result']['result_id'])
                authenticated_results.add(source_item['item_id'])
            elif source_item['failure'] is not None:
                self.evaluations.authenticate_item_failure(evaluation,source_item,profile)
        items=[]
        summary={key:0 for key in (
            'PENDING','TERMINAL','CONTRACT_VALID','CONTRACT_INVALID','ANSWERED',
            'MODEL_DISABLED',
            'ADJUDICATED','UNREVIEWED','FULLY_USABLE','PARTIAL','UNUSABLE',
            'UNSUPPORTED_CLAIM')}
        for item in evaluation['items']:
            adjudication=self._adjudication(rows.get(item['item_id']))
            result=item['result'];failure=item['failure']
            if item['state']=='PENDING':summary['PENDING']+=1
            else:summary['TERMINAL']+=1
            if result is not None:
                if result['status']=='MODEL_DISABLED':summary['MODEL_DISABLED']+=1
                elif not named_profile or item['item_id'] in authenticated_results:
                    summary['CONTRACT_VALID']+=1
                if result['status']=='ANSWERED':summary['ANSWERED']+=1
            elif failure is not None:summary['CONTRACT_INVALID']+=1
            verdict=adjudication['verdict']
            if verdict=='UNREVIEWED':summary['UNREVIEWED']+=1
            else:
                summary['ADJUDICATED']+=1;summary[verdict]+=1
                if adjudication['unsupported_claim']:summary['UNSUPPORTED_CLAIM']+=1
            items.append({
                'item_id':item['item_id'],'ordinal':item['ordinal'],
                'question_key':item['question_key'],'question':item['question'],
                'state':item['state'],
                'result_id':None if result is None else result['result_id'],
                'result_status':None if result is None else result['status'],
                'result_hash':None if result is None else result['result_hash'],
                'result_review_status':(
                    None if result is None else result['review']['status']),
                'failure_id':None if failure is None else failure['failure_id'],
                'failure_code':None if failure is None else failure['code'],
                'adjudication':adjudication,
            })
        summary['TOTAL']=len(items)
        gate_applicable=len(items)==FIXED_GATE_QUESTIONS
        profile_eligible=profile['provider']!='mock'
        if not gate_applicable:status='NOT_APPLICABLE'
        elif not profile_eligible:status='MODEL_DISABLED_PROFILE'
        elif summary['PENDING']:status='INCOMPLETE_EXECUTION'
        elif summary['UNREVIEWED']:status='INCOMPLETE_ADJUDICATION'
        elif (summary['CONTRACT_VALID']>=CONTRACT_VALID_REQUIRED
              and summary['FULLY_USABLE']>=FULLY_USABLE_REQUIRED
              and summary['UNSUPPORTED_CLAIM']==0):status='THRESHOLDS_MET'
        else:status='THRESHOLDS_NOT_MET'
        thresholds_met=status=='THRESHOLDS_MET'
        identity=[
            'reference-evaluation-scorecard-1',evaluation_id,evaluation['run_id'],
            evaluation['snapshot_id'],evaluation['question_set_hash'],
            evaluation['selector_version'],profile,
            [(item['question_key'],item['state'],item['result_hash'],item['failure_id'],
              item['adjudication']['version'],item['adjudication']['event_id'])
             for item in items],
        ]
        value={
            'scorecard_version':'reference-evaluation-scorecard-1',
            'scorecard_id':'QASCORE-'+hashlib.sha256(
                dumps(identity).encode()).hexdigest()[:32],
            'evaluation_id':evaluation_id,'project_id':evaluation['project_id'],
            'run_id':evaluation['run_id'],'snapshot_id':evaluation['snapshot_id'],
            'question_set_hash':evaluation['question_set_hash'],
            'selector_version':evaluation['selector_version'],'name':evaluation['name'],
            'profile':profile,
            'policy':'FIXED_15_HUMAN_ADJUDICATED_REFERENCE_GATE_1',
            'model_called':False,'correctness_inferred':False,
            'gate_applicable':gate_applicable,'profile_eligible':profile_eligible,
            'status':status,'thresholds_met':thresholds_met,
            'criteria':{
                'question_count_required':FIXED_GATE_QUESTIONS,
                'contract_valid_percent_required':98,
                'contract_valid_count_required':CONTRACT_VALID_REQUIRED,
                'fully_usable_count_required':FULLY_USABLE_REQUIRED,
                'unsupported_claim_count_allowed':0,
                'complete_human_adjudication_required':True,
            },
            'summary':summary,'items':items,
        }
        validate_schema('reference-evaluation-scorecard',value)
        if len(dumps(value).encode('utf-8'))>MAX_SCORECARD_BYTES:
            raise DomainError('Reference evaluation scorecard exceeds the 4 MiB bound.',409)
        return value

    def adjudicate(self,evaluation_id:str,item_id:str,verdict:str,
                   unsupported_claim:bool,expected_version:int,note:str='')->dict:
        if verdict not in VERDICTS:
            raise DomainError('Reference benchmark verdict is invalid.')
        if type(unsupported_claim) is not bool:
            raise DomainError('Reference unsupported-claim flag is invalid.')
        if type(expected_version) is not int or expected_version<0:
            raise DomainError('Reference benchmark review version is invalid.')
        if not isinstance(note,str) or len(note)>2000:
            raise DomainError('Reference benchmark review note is too long.')
        if verdict=='FULLY_USABLE' and unsupported_claim:
            raise DomainError('A fully usable answer cannot contain an unsupported claim.')
        # Authenticate every terminal item before creating an adjudication
        # event.  Otherwise a corrupt sibling could make the later scorecard
        # validation fail only after this method has written a review.
        self.scorecard(evaluation_id)
        evaluation=self.evaluations.get(evaluation_id)
        item=next((value for value in evaluation['items'] if value['item_id']==item_id),None)
        if item is None:raise DomainError('Reference evaluation item was not found.',404)
        if item['failure'] is not None:
            self.evaluations.authenticate_item_failure(evaluation,item,evaluation['profile'])
        elif 'profile_id' in evaluation['profile']:
            run=self.evaluations._run(evaluation['project_id'],evaluation['run_id'])
            if item['result'] is not None:
                self.evaluations.authenticate_item_result(
                    evaluation,item,item['result']['result_id'])
        timestamp=now();event_id=uid('QAADJ')
        with self.db.connect(True) as connection:
            item=connection.execute(
                '''SELECT i.id,i.evaluation_id,r.id AS result_id,r.status AS result_status,
                          f.id AS failure_id
                   FROM reference_evaluation_items i
                   LEFT JOIN reference_results r ON r.id=i.result_id
                   LEFT JOIN reference_evaluation_failures f ON f.item_id=i.id
                   WHERE i.id=? AND i.evaluation_id=?''',(item_id,evaluation_id)).fetchone()
            if item is None:raise DomainError('Reference evaluation item was not found.',404)
            if item['result_id'] is None and item['failure_id'] is None:
                raise DomainError('A pending Reference evaluation item cannot be adjudicated.',409)
            answered=item['result_status']=='ANSWERED'
            if verdict in ('FULLY_USABLE','PARTIAL') and not answered:
                raise DomainError(
                    'Only an answered Reference result can be fully usable or partial.',409)
            if unsupported_claim and not answered:
                raise DomainError(
                    'Only an answered Reference result can be marked with an unsupported claim.',409)
            current=connection.execute(
                '''SELECT item_id,verdict,unsupported_claim,note,version,event_id,updated_at
                   FROM reference_evaluation_adjudications WHERE item_id=?''',(item_id,)).fetchone()
            before=self._adjudication(None if current is None else dict(current))
            if before['version']!=expected_version:
                raise DomainError(
                    'Reference benchmark review changed; refresh before saving.',409)
            after={
                'verdict':verdict,'unsupported_claim':unsupported_claim,'note':note,
                'version':expected_version+1,'event_id':event_id,'updated_at':timestamp,
            }
            connection.execute(
                '''INSERT INTO reference_evaluation_adjudication_events(
                       id,evaluation_id,item_id,actor,before_json,after_json,created_at)
                   VALUES(?,?,?,?,?,?,?)''',(
                    event_id,evaluation_id,item_id,'local-engineer',
                    dumps(before),dumps(after),timestamp))
            if current is None:
                connection.execute(
                    '''INSERT INTO reference_evaluation_adjudications(
                           item_id,evaluation_id,verdict,unsupported_claim,note,version,
                           event_id,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',(
                        item_id,evaluation_id,verdict,int(unsupported_claim),note,
                        after['version'],event_id,timestamp,timestamp))
            else:
                connection.execute(
                    '''UPDATE reference_evaluation_adjudications
                       SET verdict=?,unsupported_claim=?,note=?,version=?,event_id=?,updated_at=?
                       WHERE item_id=? AND evaluation_id=?''',(
                        verdict,int(unsupported_claim),note,after['version'],event_id,
                        timestamp,item_id,evaluation_id))
        return self.scorecard(evaluation_id)

    def history(self,evaluation_id:str,item_id:str,offset:int=0,limit:int=50)->dict:
        self.db.one('''SELECT id FROM reference_evaluation_items
                       WHERE id=? AND evaluation_id=?''',(item_id,evaluation_id))
        self.evaluations.get(evaluation_id)
        total=self.db.one(
            '''SELECT COUNT(*) AS n FROM reference_evaluation_adjudication_events
               WHERE evaluation_id=? AND item_id=?''',(evaluation_id,item_id))['n']
        rows=self.db.all(
            '''SELECT id,actor,before_json,after_json,created_at
               FROM reference_evaluation_adjudication_events
               WHERE evaluation_id=? AND item_id=?
               ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?''',
            (evaluation_id,item_id,limit,offset))
        value={
            'history_version':'reference-evaluation-adjudication-history-1',
            'evaluation_id':evaluation_id,'item_id':item_id,'model_called':False,
            'total':total,'offset':offset,'limit':limit,
            'items':[
                {'event_id':row['id'],'actor':row['actor'],
                 'before':json.loads(row['before_json']),
                 'after':json.loads(row['after_json']),'created_at':row['created_at']}
                for row in rows],
        }
        validate_schema('reference-evaluation-adjudication-history',value)
        return value
