"""Exercise project-question response isolation in the offline pytest gate."""
from pathlib import Path
import shutil
import subprocess


def test_question_response_is_bound_to_project_selection_generation():
    node=shutil.which('node')
    assert node is not None,'Node is required for question project-boundary checks.'
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run(
        [node,'--test','tests/web/question_project_boundary.test.mjs'],cwd=root,
        capture_output=True,text=True,encoding='utf-8',errors='replace',check=False)
    assert result.returncode==0,result.stdout+result.stderr
