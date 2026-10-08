import pytest

from app.parsers import parse_file


ezdxf = pytest.importorskip('ezdxf')


def test_cad_long_single_text_is_split_without_tail_loss(tmp_path):
    path=tmp_path/'long-text.dxf';document=ezdxf.new();model=document.modelspace()
    value=('x'*2400)+'TAIL-MARKER'
    text=model.add_text(value);text.dxf.layer='NOTES'
    document.saveas(path)
    result=parse_file(path,path.name)
    fragments=[item for item in result['fragments'] if item['locator']['native_element_id'].startswith('cad-text:NOTES:')]
    assert len(fragments)>1
    assert ''.join(item['text'] for item in fragments)==value
    assert all(len(item['text'])<=1200 for item in fragments)
    assert result['status']=='SUCCESS' and result['warnings']==[]


def test_cad_same_layer_51_texts_keep_each_handle_and_final_tail(tmp_path):
    path=tmp_path/'many-texts.dxf';document=ezdxf.new();model=document.modelspace()
    expected={}
    for index in range(51):
        value=f'note-{index}-'+('x'*32)
        if index==50:
            value=('x'*80)+'TAIL-MARKER'
        entity=model.add_text(value);entity.dxf.layer='NOTES'
        expected[entity.dxf.handle]=value
    document.saveas(path)
    result=parse_file(path,path.name)
    text_fragments=[item for item in result['fragments'] if item['locator']['native_element_id'].startswith('cad-text:NOTES:')]
    actual={}
    for item in text_fragments:
        _, layer, handle, part=item['locator']['native_element_id'].split(':')
        assert layer=='NOTES'
        actual[handle]=actual.get(handle,'')+item['text']
        assert part.isdigit()
    assert actual==expected
    assert actual[next(reversed(expected))].endswith('TAIL-MARKER')
    assert result['status']=='SUCCESS' and result['warnings']==[]


def test_cad_minsert_count_uses_array_instances_and_retains_basis(tmp_path):
    path=tmp_path/'array.dxf';document=ezdxf.new();document.blocks.new('PUMP')
    insert=document.modelspace().add_blockref('PUMP',(0,0));insert.dxf.layer='EQUIPMENT'
    insert.dxf.row_count=2;insert.dxf.column_count=3
    insert.dxf.row_spacing=10;insert.dxf.column_spacing=20
    assert insert.mcount==6
    document.saveas(path)
    result=parse_file(path,path.name)
    takeoff=next(item for item in result['takeoffs'] if item['kind']=='BLOCK_COUNT')
    assert takeoff['value']==6 and takeoff['entity_ids']==[insert.dxf.handle]
    assert 'rows=2' in takeoff['scope_note'] and 'columns=3' in takeoff['scope_note']
    assert 'row_spacing=10' in takeoff['scope_note'] and 'column_spacing=20' in takeoff['scope_note']
    assert 'mcount=6' in takeoff['scope_note']


@pytest.mark.parametrize(('row_spacing','column_spacing','expected'),[(0,0,1),(10,0,2),(0,10,3)])
def test_cad_minsert_zero_spacing_uses_ezdxf_effective_mcount(tmp_path,row_spacing,column_spacing,expected):
    path=tmp_path/f'array-{row_spacing}-{column_spacing}.dxf';document=ezdxf.new();document.blocks.new('PUMP')
    insert=document.modelspace().add_blockref('PUMP',(0,0));insert.dxf.layer='EQUIPMENT'
    insert.dxf.row_count=2;insert.dxf.column_count=3
    insert.dxf.row_spacing=row_spacing;insert.dxf.column_spacing=column_spacing
    assert insert.mcount==expected
    document.saveas(path)
    result=parse_file(path,path.name)
    takeoff=next(item for item in result['takeoffs'] if item['kind']=='BLOCK_COUNT')
    assert takeoff['value']==expected
    assert f'mcount={expected}' in takeoff['scope_note']


def test_cad_layer_text_resource_limit_is_partial_and_warns(tmp_path):
    path=tmp_path/'over-cap.dxf';document=ezdxf.new();text=document.modelspace().add_text('x'*24001)
    text.dxf.layer='NOTES';document.saveas(path)
    result=parse_file(path,path.name)
    assert result['status']=='PARTIAL'
    assert any('文字超过资源限额' in warning for warning in result['warnings'])


def test_cad_text_extraction_failure_is_partial_with_warning(tmp_path,monkeypatch):
    from app import cad

    path=tmp_path/'broken-text.dxf';path.write_text('synthetic',encoding='utf-8')

    class BrokenDxf:
        layer='NOTES';handle='BAD'
        def get(self,name,default=None):
            return getattr(self,name,default)
        @property
        def text(self):
            raise ValueError('broken text')

    class BrokenText:
        dxf=BrokenDxf()
        def dxftype(self):
            return 'TEXT'

    class Document:
        header={}
        def modelspace(self):
            return [BrokenText()]

    monkeypatch.setattr(ezdxf,'readfile',lambda _path:Document())
    result=cad.parse_cad(path,path.name)
    assert result['status']=='PARTIAL'
    assert any('文字对象BAD无法提取：ValueError' in warning for warning in result['warnings'])
