"""Zero-call input-scope and operational readiness manifest for one evaluation."""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.db import Database,DomainError,dumps
from app.reference_evaluations import ReferenceEvaluationStore
from contracts.runtime_rules import validate_schema


MAX_READINESS_BYTES=4*1024*1024


class ReferenceReadiness:
    def __init__(self,db:Database,evaluations:ReferenceEvaluationStore,
                 current_settings:Callable[[],object],select_pages:Callable):
        self.db=db;self.evaluations=evaluations
        self.current_settings=current_settings;self.select_pages=select_pages

    @staticmethod
    def _page(page)->dict:
        issue_dates=[value for value in page.issue_dates if value][:64]
        revision_labels=[value for value in page.revision_labels if value][:64]
        return {
            'page_key':page.page_key,'document_id':page.document_id,
            'file_name':page.file_name,'page_number':page.page_number,
            'score':page.score,'matched_terms':list(page.matched_terms),
            'reason_codes':list(page.reason_codes),
            'evidence_count':len(page.source_evidence_ids),
            'source_text_bytes':page.source_text_bytes,
            'selected_text_bytes':page.selected_text_bytes,
            'issue_date_count':len([value for value in page.issue_dates if value]),
            'issue_dates':issue_dates,
            'revision_label_count':len([value for value in page.revision_labels if value]),
            'revision_labels':revision_labels,
        }

    def build(self,evaluation_id:str)->dict:
        evaluation=self.evaluations.get(evaluation_id)
        context=self.evaluations.item_context(
            evaluation_id,evaluation['items'][0]['item_id'])
        run=context['run'];settings=self.current_settings()
        frozen_profile=evaluation['profile']
        active_profile=self.evaluations.profile(settings)
        try:
            self.evaluations.require_profile(evaluation_id,settings);profile_match=True
        except DomainError:profile_match=False
        provider_ready=bool(settings.provider!='mock' and not settings.live_errors())
        unresolved_count=len(self.db.unresolved_calls(evaluation['project_id']))
        active_analysis=bool(self.db.one(
            "SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING') LIMIT 1",required=False))
        active_verification=bool(self.db.one(
            """SELECT id FROM verification_jobs
                WHERE state IN ('QUEUED','RUNNING') LIMIT 1""",required=False))
        active_job=bool(self.db.one(
            """SELECT id FROM reference_evaluation_jobs
                WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED') LIMIT 1""",required=False))
        pending=evaluation['summary']['PENDING']
        blockers=[]
        if not profile_match:blockers.append('ACTIVE_PROFILE_MISMATCH')
        if not provider_ready:blockers.append(
            'MODEL_DISABLED' if settings.provider=='mock' else 'PROVIDER_NOT_READY')
        if unresolved_count:blockers.append('UNRESOLVED_CALLS')
        if active_analysis:blockers.append('ACTIVE_ANALYSIS')
        if active_verification:blockers.append('ACTIVE_VERIFICATION')
        if active_job:blockers.append('ACTIVE_MANAGED_JOB')
        if not pending:blockers.append('NO_PENDING_QUESTIONS')

        questions=[];page_references=[];documents=set();selected_bytes=0
        pending_page_references=0;pending_selected_bytes=0
        warning_counts={key:0 for key in (
            'NO_SELECTED_SOURCE','SOURCE_VERSION_CONFLICT','CANDIDATE_LIMIT_REACHED',
            'PAGE_LIMIT_REACHED','BYTE_LIMIT_REACHED')}
        for item in evaluation['items']:
            selection=self.select_pages(
                self.db,run,item['question'],
                selector_version=evaluation['selector_version'])
            pages=[self._page(page) for page in selection.selected_pages]
            conflicts=list(selection.source_conflicts[:20])
            warnings=[]
            if not pages:warnings.append('NO_SELECTED_SOURCE')
            if selection.source_conflicts:warnings.append('SOURCE_VERSION_CONFLICT')
            if selection.excluded_by_candidate_limit:warnings.append('CANDIDATE_LIMIT_REACHED')
            if selection.excluded_by_page_limit:warnings.append('PAGE_LIMIT_REACHED')
            if selection.excluded_by_byte_limit:warnings.append('BYTE_LIMIT_REACHED')
            for warning in warnings:warning_counts[warning]+=1
            selected_bytes+=selection.byte_count
            if item['state']=='PENDING':
                pending_page_references+=len(pages)
                pending_selected_bytes+=selection.byte_count
            for page in pages:
                page_references.append(page['page_key']);documents.add(page['document_id'])
            questions.append({
                'ordinal':item['ordinal'],'question_key':item['question_key'],
                'question':item['question'],'item_state':item['state'],
                'would_dispatch':item['state']=='PENDING',
                'selection_id':selection.selection_id,
                'candidate_pages_considered':selection.candidate_pages_considered,
                'selected_page_count':len(pages),'selected_text_bytes':selection.byte_count,
                'excluded_by_candidate_limit':selection.excluded_by_candidate_limit,
                'excluded_by_page_limit':selection.excluded_by_page_limit,
                'excluded_by_byte_limit':selection.excluded_by_byte_limit,
                'source_conflict_count':len(selection.source_conflicts),
                'source_conflicts':conflicts,
                'warnings':warnings,'pages':pages,
            })
        unique_pages=set(page_references)
        manifest_identity=[
            'reference-evaluation-readiness-1',evaluation['evaluation_id'],
            evaluation['run_id'],evaluation['snapshot_id'],evaluation['question_set_hash'],
            [(item['question_key'],item['selection_id']) for item in questions],
        ]
        manifest_id='QARDY-'+hashlib.sha256(
            dumps(manifest_identity).encode()).hexdigest()[:32]
        checks={
            'profile_match':profile_match,'provider_ready':provider_ready,
            'unresolved_call_count':unresolved_count,
            'active_analysis':active_analysis,'active_verification':active_verification,
            'active_managed_job':active_job,
        }
        status_identity=[manifest_id,active_profile,checks,blockers,
                         [(item['question_key'],item['item_state']) for item in questions]]
        status_id='QARDYST-'+hashlib.sha256(
            dumps(status_identity).encode()).hexdigest()[:32]
        value={
            'readiness_version':'reference-evaluation-readiness-1',
            'manifest_id':manifest_id,'status_id':status_id,
            'evaluation_id':evaluation['evaluation_id'],
            'project_id':evaluation['project_id'],'run_id':evaluation['run_id'],
            'snapshot_id':evaluation['snapshot_id'],
            'question_set_hash':evaluation['question_set_hash'],
            'selector_version':evaluation['selector_version'],
            'policy':'LOCAL_LITERAL_SELECTION_MANIFEST_NO_MODEL_CALL',
            'model_called':False,'source_text_included':False,
            'semantic_graph_used':False,'embeddings_used':False,
            'status':('NO_PENDING' if not pending else 'READY' if not blockers else 'BLOCKED'),
            'ready_to_confirm':not blockers,
            'blockers':blockers,'checks':checks,
            'frozen_profile':frozen_profile,'active_profile':active_profile,
            'summary':{
                'question_count':len(questions),'pending_question_count':pending,
                'maximum_remaining_model_decisions':pending*3,
                'maximum_page_images_per_decision':3 if frozen_profile['vision_enabled'] else 0,
                'maximum_remaining_page_image_attachments':(
                    pending*9 if frozen_profile['vision_enabled'] else 0),
                'selected_question_count':sum(bool(item['pages']) for item in questions),
                'no_selected_source_count':warning_counts['NO_SELECTED_SOURCE'],
                'total_page_references':len(page_references),
                'pending_page_references':pending_page_references,
                'unique_page_count':len(unique_pages),
                'repeated_page_references':len(page_references)-len(unique_pages),
                'unique_document_count':len(documents),
                'sum_selected_text_bytes':selected_bytes,
                'sum_pending_selected_text_bytes':pending_selected_bytes,
                'warning_question_counts':warning_counts,
            },
            'questions':questions,
        }
        validate_schema('reference-evaluation-readiness',value)
        if len(dumps(value).encode('utf-8'))>MAX_READINESS_BYTES:
            raise DomainError('Reference evaluation readiness exceeds the 4 MiB bound.',409)
        return value
