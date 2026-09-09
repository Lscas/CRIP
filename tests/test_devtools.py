"""DEV-LOWCOST-001 / DEV-CHECK-001：真实脚本，不验证账户价格。"""
import json,tomllib
from pathlib import Path
import pytest
from scripts.context_pack import build,ROOT

def test_context_pack_is_bounded_and_contains_only_selected_requirements():
    out=build('DEV-002')
    assert len(out)<=14000 and 'FR-OPTIONS-001' in out
    assert '全量读取所有文件' not in out
    assert 'api_key=' not in out.lower()

def test_context_rejects_path_traversal():
    with pytest.raises(ValueError):build('../.env')

def test_task_references_have_real_context_files():
    for p in (ROOT/'tasks').glob('*.json'):
        data=json.loads(p.read_text(encoding="utf-8"));assert not data['paid_calls_allowed']
        for name in data['context_files']:assert (ROOT/name).is_file()
        assert len(build(data['task_id']))<=data['max_context_chars']

def test_codex_config_is_small_and_does_not_weaken_security():
    data=tomllib.loads((ROOT/'.codex/config.toml').read_text(encoding="utf-8"))
    assert data=={'model_reasoning_effort':'low'}
    assert len((ROOT/'AGENTS.md').read_text(encoding="utf-8"))<3000
