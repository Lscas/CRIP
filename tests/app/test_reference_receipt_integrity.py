"""Complete-source telemetry cannot contradict its actual input manifest."""
from copy import deepcopy
import hashlib

import pytest

from app.db import DomainError, dumps
from .test_reference_cases import _followup_result
from .test_reference_results import _saved_run


@pytest.mark.parametrize('mutation', ['evidence_count', 'image_bytes', 'duplicate_evidence'])
def test_v8_receipt_rejects_inconsistent_input_counts(client, project, mutation):
    db, _, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    question = 'What approved color applies to Finish key PT9?'
    run, result_id = _followup_result(db, client, project, document, question)
    store = client.app.state.reference_results
    result = deepcopy(store.get(result_id)['result'])
    receipt = result['execution_receipts'][0]
    if mutation == 'evidence_count':
        receipt['evidence_count'] += 1
    elif mutation == 'image_bytes':
        receipt['image_bytes'] += 1
    else:
        duplicate = {**receipt['evidence_inputs'][0], 'text_sha256': 'f' * 64}
        receipt['evidence_inputs'].append(duplicate)
        receipt['evidence_count'] = 2
        receipt['source_text_bytes'] *= 2
        receipt['ordered_evidence_manifest_sha256'] = hashlib.sha256(
            dumps(receipt['evidence_inputs']).encode()).hexdigest()
    with pytest.raises(DomainError, match='input counts'):
        store.save(run, question, result, 'mock', 'mock-no-network')
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n'] == 1
