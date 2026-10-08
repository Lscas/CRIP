"""Internal three-decision PDF projection loop, with no public answer publication.

Recovery always starts from the same initial proof. The Gateway reauthenticates
settled steps; their requests alone authorize deterministic local acquisition.
"""
from __future__ import annotations

from app.db import DomainError
from app.gateway import InvalidModelOutput
from app.page_selector import select_pages
from app.reference_projection_decision import validate_projection_decision_for
from app.reference_projection_input import prepare_projection_input
from app.reference_projection_loop_receipt import request_history
from app.reference_projection_preview import projection_preview_proof
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, ProjectionProtocol, resolve_projection_protocol
from app.reference_projection_stage import (
    prepare_projection_stage, append_projection_stage, authenticate_projection_stage,
)


class ProjectProjectionLoop:
    def __init__(self, db, gateway=None, uploads=None, *,
                 protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1):
        self.db = db
        self.gateway = gateway
        self.uploads = uploads
        self.protocol = resolve_projection_protocol(protocol)

    def _initial(self, run: dict, question: str):
        from app.evidence_loop import ProjectEvidenceLoop, _answer_parts
        ProjectEvidenceLoop._check_run(run)
        if self.uploads is None:
            raise DomainError('Projection loop requires the local source store.', 409)
        selection = select_pages(self.db, run, question, selector_version='literal-page-selector-9')
        base = prepare_projection_input(self.db, self.uploads, run, selection, question, _answer_parts(question))
        return prepare_projection_stage(base, self.db, self.uploads), selection

    def preview(self, run: dict, question: str, route: dict) -> dict:
        stage, selection = self._initial(run, question)
        return {
            'qa_version': '3', 'feature': 'APPEND_ONLY_PROJECTION_LOOP',
            'loop_version': self.protocol.loop_version, 'status': 'LOOP_READY', 'model_called': False,
            'max_model_decisions': 3, 'max_evidence_rounds': 2,
            'allowed_tools': ['FIND_IDENTIFIER', 'SEARCH_TEXT'],
            'page_selection': selection.public(), 'execution_profile': route,
            'preview_proof': projection_preview_proof(run, stage, route, protocol=self.protocol),
            'projection_complete': stage.context['projection_complete'],
            'projection_row_count': len(stage.context['rows']),
        }

    def ask(self, run: dict, question: str, route: dict, *, preview_proof: dict | None) -> dict:
        stage, _ = self._initial(run, question)
        if preview_proof != projection_preview_proof(run, stage, route, protocol=self.protocol):
            raise DomainError('PREVIEW_STALE: Preview this projected question and profile again before asking.', 409)
        chain = []
        trace = []
        receipts = []
        new_calls = 0
        accepted_requests = 0
        supplement_rounds = 0

        def output(status, reason, missing, *, review_packet=None, local_exhausted=False):
            return {
                'qa_version': '3', 'feature': 'APPEND_ONLY_PROJECTION_LOOP',
                'loop_version': self.protocol.loop_version, 'run_id': run['id'], 'question': question,
                'status': status, 'reason_code': reason, 'answer': '', 'claims': [], 'calculations': [],
                'missing': missing, 'answer_basis': (
                    'PROJECTION_REVIEW_REQUIRED' if status == 'REVIEW_REQUIRED'
                    else 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED' if (
                        reason == 'NO_NEW_EVIDENCE' and (
                            local_exhausted or self.protocol == PROJECTION_PROTOCOL_V1))
                    else 'PROJECTION_LOOP_TERMINAL'),
                'source_scope': {'snapshot_id': stage.snapshot_id,
                                 'policy': 'RUN_SNAPSHOT_APPEND_ONLY',
                                 'conflicts': stage.context['source_conflicts']},
                'model_called': bool(new_calls), 'model_call_count': new_calls,
                'decision_count': len(receipts), 'supplement_round_count': supplement_rounds,
                'accepted_supplement_request_count': accepted_requests,
                'projection_row_count': len(stage.context['rows']),
                'execution_profile': route, 'execution_receipts': receipts,
                'decision_trace': trace, 'review_packet': review_packet,
                'preview_proof': preview_proof,
            }

        if self.gateway is None or self.gateway.s.provider == 'mock':
            return output('MODEL_DISABLED', 'MODEL_DISABLED', [])
        for index in range(3):
            try:
                decision_call = (self.gateway._projection_loop_decision_v10 if self.protocol == PROJECTION_PROTOCOL_V1
                                 else self.gateway._projection_loop_decision_v10_loop2)
                result = decision_call(run, question, stage, route, prior_chain=chain, preview_proof=preview_proof)
            except InvalidModelOutput as exc:
                if isinstance(exc.execution_receipt, dict):
                    exc.failure_execution = {
                        'failure_execution_version': self.protocol.failure_version,
                        'receipt_scope': 'COMPLETE_CHAIN',
                        'execution_receipts': receipts + [exc.execution_receipt],
                        'supplement_round_count': supplement_rounds,
                        'accepted_supplement_request_count': accepted_requests,
                    }
                raise
            decision = result.data
            receipts.append(result.execution_receipt)
            new_calls += int(not result.cached)
            trace.append({'round': index + 1, 'status': decision['status'],
                          'reason_code': decision['reason_code'],
                          'missing_facts': decision['missing_facts'],
                          'requests': decision['requests'], 'cached': result.cached})
            if decision['status'] == 'REVIEW_REQUIRED':
                authenticate_projection_stage(stage, self.db, self.uploads)
                validate_projection_decision_for(self.protocol, decision, stage,
                                             request_history=request_history(chain, protocol=self.protocol),
                                             remaining_decisions=3-index)
                rows = {row['row_ref']: row for row in stage.context['rows']}
                packet = {
                    'packet_version': 'projection-review-packet-2', 'status': 'REVIEW_REQUIRED',
                    'answer_basis': 'PROJECTION_REVIEW_REQUIRED',
                    'projection_input_sha256': stage.projection_input_sha256,
                    'projection_context_sha256': stage.projection_context_sha256,
                    'source_bindings': [{**rows[item['row_ref']]['binding'], **item}
                                        for item in decision['selections']],
                    'source_text_included': False, 'chain_of_thought_included': False,
                    'verification': {'source_projection_integrity': True,
                                     'part_selection_coverage_complete': True,
                                     'object_condition_relations_verified': False,
                                     'answer_completeness_verified': False},
                }
                return output(decision['status'], decision['reason_code'], [], review_packet=packet)
            if decision['status'] != 'NEED_EVIDENCE':
                return output(decision['status'], decision['reason_code'], decision['missing_facts'])
            # The Gateway has authenticated the settled response and receipt.
            # Never accept requests directly from an API caller or a preview.
            chain.append({'receipt': result.execution_receipt, 'decision': decision})
            next_stage = append_projection_stage(stage, decision['requests'], self.db, self.uploads)
            accepted_requests += len(decision['requests'])
            supplement_rounds += 1
            stage = next_stage
            if stage.added_page_count == 0:
                return output('CANNOT_ANSWER', 'NO_NEW_EVIDENCE', [
                    {'part_ref': part_ref, 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}
                    for part_ref in dict.fromkeys(gap['part_ref'] for gap in decision['missing_facts'])],
                    local_exhausted=True)
        raise DomainError('Projection decision limit reached without a terminal decision.', 409)
