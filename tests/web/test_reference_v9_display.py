from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def sources():
    return (
        (ROOT / 'web' / 'app.js').read_text(encoding='utf-8'),
        (ROOT / 'web' / 'index.html').read_text(encoding='utf-8'),
    )


def test_v9_controls_are_hidden_until_the_server_explicitly_enables_them():
    app, html = sources()
    assert "reference_layout_v9?.enabled===true" in app
    assert "node.closest('label').hidden=!enabled" in app
    assert 'id="project-question-profile"' in html
    assert 'id="reference-question-profile"' in html
    assert 'id="reference-evaluation-profile"' in html
    assert html.count('hidden="" value="FLASH_NONE"') >= 2


def test_v9_preview_and_ask_keep_the_complete_route_and_proof_together():
    app, _ = sources()
    assert "selector_version:'literal-page-selector-9',profile_id:profile" in app
    assert "preview_proof:proof.proof" in app
    assert "run_id:proof.proof.run_id,question,selector_version:'literal-page-selector-9'" in app
    assert "await api(`/projects/${project}/questions`,'POST',{question})" in app
    assert 'state.questionRequestVersion===requestVersion' in app


def test_v9_proof_is_invalidated_by_input_project_run_or_reference_knowledge_changes():
    app, _ = sources()
    assert "$('project-question').oninput=()=>{state.projectV9Preview=null" in app
    assert "$('reference-question-profile').onchange=()=>{state.referenceV9Preview=null" in app
    assert "state.project=id;referenceKnowledgeFingerprint='';clearV9Previews()" in app
    assert "state.run=$('run-select').value;clearV9Previews()" in app
    assert "syncReferenceKnowledgeProof(){" in app
    assert "referenceKnowledgeFingerprint!==next)clearV9Previews()" in app


def test_v9_evaluation_and_case_followup_send_named_fields_and_keep_case_proof_separate():
    app, _ = sources()
    assert "id='reference-case-v9-profile'" in app
    assert "selector_version:'literal-page-selector-9',profile_id:proof.profile,preview_proof:proof.proof" in app
    assert "if(!proof.profile){const freshPreview=" in app
    assert "...(profile?{selector_version:'literal-page-selector-9',profile_id:profile}:{})" in app


def test_v9_case_never_posts_without_a_proof_while_v8_keeps_its_existing_shape():
    app, _ = sources()
    assert "profile.value&&!v9CaseProofValid(preview,renderedProject,item.question,profile.value,run?.id)" in app
    assert "proof.profile?{run_id:runId,question:item.question,selector_version:'literal-page-selector-9',profile_id:proof.profile,preview_proof:proof.proof}:{run_id:runId,question:item.question}" in app


def test_v9_pending_answers_guard_full_scope_and_freeze_profile_controls():
    app, _ = sources()
    assert "$('project-question-profile').disabled=state.asking||state.caseFlowBusy" in app
    assert "$('reference-question-profile').disabled=state.referenceAsking||state.referencePreviewing" in app
    assert "$('reference-evaluation-profile').disabled=state.referenceEvaluationBusy" in app
    assert "if(profile&&!current())" in app
    assert "loadReferenceResults(profile?{isCurrent:current}:{})" in app
    assert "if(options.isCurrent&&!options.isCurrent())return" in app


def test_v9_clone_requires_an_explicit_named_target_and_keeps_v8_body_unchanged():
    app, _ = sources()
    assert "cloneProfile.dataset.testid='reference-v9-clone-profile'" in app
    assert "targetProfile===evaluation.profile.profile_id" in app
    assert "layoutBound?{profile_id:targetProfile}:undefined" in app
