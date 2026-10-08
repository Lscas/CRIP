"""DEV-132: retrieval benchmark emits fact coverage, never source text."""
from scripts.benchmark_question_retrieval import EXPECTATIONS,compare,score_question


def test_locked_benchmark_covers_every_fixed_question():
    assert list(EXPECTATIONS)==[
        'S1','S2','S3','S4','S5','D1','D2','D3','D4','D5','C1','C2','C3','C4','C5']
    assert all(checks and len({label for label,_ in checks})==len(checks)
               for checks in EXPECTATIONS.values())


def test_score_question_reports_labels_without_returning_evidence_text():
    evidence=[{'raw_text':('General 7 Architectural 48 Structural 13 Mechanical 20 '
                           'Fire Protection 8 Plumbing 19'),
               'locator':{'section':'Drawing index','paragraph':'Table 1 row 2','sheet':'G0.01'}}]

    score=score_question('S5',evidence)

    assert score['complete'] and score['atoms_found']==score['atoms_total']==6
    assert 'raw_text' not in score and 'General 7' not in repr(score)


def test_compare_exposes_new_and_regressed_atoms_without_source_text():
    baseline={'questions_complete':0,'atoms_found':1,'questions':[{
        'id':'S1','complete':False,'found':['project_name'],'missed':['doe_job'],
        'atoms_found':1,'atoms_total':2,'retrieved_count':1}]}
    candidate={'questions_complete':0,'atoms_found':1,'questions':[{
        'id':'S1','complete':False,'found':['doe_job'],'missed':['project_name'],
        'atoms_found':1,'atoms_total':2,'retrieved_count':1}]}

    result=compare(baseline,candidate)

    assert result['changes']==[{
        'id':'S1','atom_delta':0,'newly_found':['doe_job'],'regressed':['project_name']}]
    assert result['raw_evidence_in_report'] is False and result['provider_calls_made']==0
