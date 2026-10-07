"""Keep the projection-case DOM boundary in the offline pytest gate."""
from pathlib import Path
import shutil
import subprocess


def test_projection_cases_render_with_identity_and_legacy_boundaries():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'web' / 'app.js').read_text(encoding='utf-8')
    translations = (root / 'web' / 'i18n.js').read_text(encoding='utf-8')
    for token in ('projectionCaseAvailable', 'projectionCaseIdentityCurrent',
                  'projectionCaseSourceButton', 'savedFollowupCandidate',
                  'reference.caseProjectionLegacyQuestion',
                  'reference.caseFollowupHistoricalProof',
                  'reference.caseSourceWorkflowUnavailable'):
        assert token in source or token in translations
    assert "case_view_version==='reference-case-view-2'" in source
    assert "question_creation_available===false" in source
    node = shutil.which('node')
    assert node is not None, 'Node is required for projection case checks.'
    result = subprocess.run(
        [node, '--test', 'tests/web/reference_projection_cases.test.mjs'], cwd=root,
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    assert result.returncode == 0, result.stdout + result.stderr
