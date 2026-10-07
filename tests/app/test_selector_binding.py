"""Real selector-to-navigation tests; V9 has no production model route yet."""
import copy
import json

import pytest

from app import page_selector as selector
from tests.app.test_page_selector import _raw_run,_spatial_text



def _two_columns():
    left,left_map=_spatial_text([(10,[('Panel',10,36),('A',40,48),('511',54,74)])])
    right,right_map=_spatial_text([(10,[('Panel',300,326),('B',330,338),('511',344,364)])])
    return left,left_map,right,right_map


def test_v8_rows_and_public_preview_keep_legacy_shape(client,project):
    left,left_map,right,right_map=_two_columns()
    db,run,_=_raw_run(client,project,[
        {'page':1,'sheet':'A5.01','text':left,'text_map':left_map},
        {'page':1,'sheet':'A5.01','text':right,'text_map':right_map},
    ])
    question='On Sheet A5.01, which information applies to Panel A and Panel B?'
    actual=selector.select_pages(db,run,question,selector_version=selector.COMPLETE_SELECTOR_VERSION)
    assert actual.public()['context_policy']=='COMPLETE_SELECTED_SCOPE_V1'
    assert actual.evidence_rows[0]['layout_lines']=='Panel A 511\n||\nPanel B 511'
    assert all('extraction_method' not in row and 'text_map' not in row for row in actual.evidence_rows)
    assert selector.COMPLETE_SELECTOR_VERSION=='literal-page-selector-8'
    assert selector.COMPLETE_SELECTOR_VERSIONS==('literal-page-selector-8',selector.LAYOUT_BOUND_SELECTOR_VERSION)


def test_v9_select_binding_and_model_navigation_are_complete_and_non_citable(client,project):
    left,left_map,right,right_map=_two_columns()
    continuation='蓝😀 complete continuation. '*2800
    db,run,_=_raw_run(client,project,[
        {'page':1,'sheet':'A5.01','text':left,'text_map':left_map},
        {'page':1,'sheet':'A5.01','text':right,'text_map':right_map},
        {'page':2,'sheet':'A5.01','text':continuation},
    ])
    selection=selector.select_pages(db,run,
        'On Sheet A5.01, which information applies to Panel A and Panel B?',
        selector_version=selector.LAYOUT_BOUND_SELECTOR_VERSION)
    assert selection.selector_version==selector.LAYOUT_BOUND_SELECTOR_VERSION
    assert selection.byte_count>64_000 and selection.public()['source_text_clipped'] is False
    assert all('extraction_method' in row and 'text_map' in row for row in selection.evidence_rows)
    assert all('layout_lines' not in row for row in selection.evidence_rows)
    binding=selector.build_layout_binding(list(selection.evidence_rows))
    navigation=selector.model_layout_navigation(list(selection.evidence_rows))
    selector.verify_layout_binding(binding,list(selection.evidence_rows))
    assert binding['page_layouts'][0]['model_navigation'][0][0]['parts'][-1]['evidence_id']=='EV-PAGE-1'
    assert binding['page_layouts'][0]['model_navigation'][1][0]['parts'][-1]['evidence_id']=='EV-PAGE-2'
    encoded=json.dumps(navigation,ensure_ascii=False)
    assert 'EV-PAGE-' not in encoded and 'bbox' not in encoded and 'start' not in encoded and 'sha256' not in encoded
    assert navigation['limitations']==['Navigation is non-citable and does not establish semantic support.']
    assert navigation['pages'][0]['columns'][0][0]['parts'][-1]=={'text':'511','ref':'E1'}
    assert navigation['pages'][0]['columns'][1][0]['parts'][-1]=={'text':'511','ref':'E2'}
    assert selection.public()['context_policy']=='COMPLETE_SELECTED_SCOPE_WITH_LAYOUT_BINDING_V1'
    assert selection.public()['max_pages'] is None and selection.public()['layout_available_pages']==1


def test_navigation_rebuilds_aliases_after_reorder_and_marks_only_failed_maps_unbound():
    rows=[
        {'evidence_id':'A','document_id':'D1','raw_text':'A 511','extraction_method':'TEXT_LAYER',
         'locator':{'page_number':1},'text_map':[{'start':0,'end':1,'bbox':[10,10,20,20]},{'start':2,'end':5,'bbox':[24,10,40,20]}]},
        {'evidence_id':'B','document_id':'D1','raw_text':'B 511','extraction_method':'TEXT_LAYER',
         'locator':{'page_number':1},'text_map':[{'start':0,'end':1,'bbox':[300,10,310,20]},{'start':2,'end':5,'bbox':[314,10,330,20]}]},
        {'evidence_id':'C','document_id':'D1','raw_text':'missing map','locator':{'page_number':1}},
        {'evidence_id':'D','document_id':'D2','raw_text':'one valid column','extraction_method':'TEXT_LAYER',
         'locator':{'page_number':7},'text_map':[{'start':0,'end':3,'bbox':[10,10,20,20]}]},
    ]
    first=selector.model_layout_navigation(rows)
    assert first['unbound_refs']==[{'ref':'E3','reason':'NOT_TEXT_LAYER'}]
    assert len(first['pages'])==1 and first['pages'][0]['document_id']=='D1'
    assert first['pages'][0]['columns'][0][0]['parts'][0]['ref']=='E1'
    reordered=selector.model_layout_navigation([rows[1],rows[0],rows[2],rows[3]])
    assert reordered['pages'][0]['columns'][0][0]['parts'][0]['ref']=='E2'
    refs={part['ref'] for page in reordered['pages'] for column in page['columns'] for line in column for part in line['parts']}
    assert refs=={'E1','E2'} and reordered['unbound_refs']==[{'ref':'E3','reason':'NOT_TEXT_LAYER'}]
    with pytest.raises(selector.LayoutBindingError):selector.model_layout_navigation([rows[0],rows[0]])
    with pytest.raises(selector.LayoutBindingError):selector.model_layout_navigation([{'raw_text':'no stable id'}])
    close=copy.deepcopy(rows[:2])
    close[1]['text_map'][0]['bbox']=[42,10,52,20];close[1]['text_map'][1]['bbox']=[56,10,72,20]
    close.append({'evidence_id':'R','document_id':'D1','raw_text':'RIGHT','extraction_method':'TEXT_LAYER',
                  'locator':{'page_number':1},'text_map':[{'start':0,'end':5,'bbox':[220,10,250,20]}]})
    small_gap=selector.model_layout_navigation(close)
    assert small_gap['pages'][0]['columns'][0][0]['parts']==[
        {'text':'A','ref':'E1'},{'text':'511','ref':'E1'},
        {'text':'B','ref':'E2'},{'text':'511','ref':'E2'}]


@pytest.mark.parametrize('limit',[None,0,7,8,20])
def test_legacy_wrapper_parity_keeps_legacy_rows_tolerant(limit):
    text='蓝😀 511'
    legacy={'extraction_method':'TEXT_LAYER','text_map':[
        {'start':0,'end':2,'bbox':[1,1,8,5]},{'start':3,'end':6,'bbox':[200,1,212,5]}]}
    expected='蓝😀\n||\n511' if limit in (None,20) else None
    assert selector._page_layout_columns([(legacy,text)],max_bytes=limit)==expected


def test_binding_rejects_changed_complete_object_not_only_navigation_projection():
    left,left_map,right,right_map=_two_columns()
    rows=[
        {'evidence_id':'A','document_id':'D','raw_text':left,'extraction_method':'TEXT_LAYER','locator':{'page_number':1},'text_map':left_map},
        {'evidence_id':'B','document_id':'D','raw_text':right,'extraction_method':'TEXT_LAYER','locator':{'page_number':1},'text_map':right_map},
    ]
    binding=selector.build_layout_binding(rows)
    altered=copy.deepcopy(binding);altered['sources'][0]['unmapped_ranges']=[[0,1]]
    body={key:value for key,value in altered.items() if key!='binding_sha256'}
    altered['binding_sha256']=__import__('hashlib').sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(selector.LayoutBindingError):selector.verify_layout_binding(altered,rows)


def test_legacy_bbox_numeric_subclasses_remain_accepted_but_booleans_do_not():
    class Coordinate(float):pass
    class PageCoordinate(int):pass
    mapping=[{'start':0,'end':1,'bbox':[PageCoordinate(1),Coordinate(1),PageCoordinate(8),Coordinate(5)]},
             {'start':2,'end':3,'bbox':[PageCoordinate(200),Coordinate(1),PageCoordinate(212),Coordinate(5)]}]
    legacy={'extraction_method':'TEXT_LAYER','text_map':mapping}
    assert selector._page_layout_columns([(legacy,'A B')],max_bytes=None)=='A\n||\nB'
    mapping[0]['bbox'][0]=True
    assert selector._page_layout_columns([(legacy,'A B')],max_bytes=None) is None
