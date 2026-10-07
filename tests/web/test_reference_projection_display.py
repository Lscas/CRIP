"""Keep the offline production-renderer boundary in the normal pytest gate."""
from pathlib import Path
import shutil
import subprocess


def test_projection_result_renderer_never_reuses_legacy_answer_actions():
    root=Path(__file__).resolve().parents[2]
    node=shutil.which('node')
    assert node is not None, 'Node is required for projection display checks.'
    result=subprocess.run(
        [node,'--test','tests/web/reference_projection_display.test.mjs'],cwd=root,
        capture_output=True,text=True,encoding='utf-8',errors='replace',check=False)
    assert result.returncode==0,result.stdout+result.stderr
