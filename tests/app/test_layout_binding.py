"""Production geometry/source-binding regressions; original stage negative cases retained."""
import copy
import hashlib
import json
import pytest
from app import page_selector as s
def row(eid,text,page,words,doc='D',method='TEXT_LAYER',**extra):
 d={'evidence_id':eid,'document_id':doc,'raw_text':text,'extraction_method':method,'locator':({'page_number':page} if page else {}),'text_map':[{'start':text.index(t,o),'end':text.index(t,o)+len(t),'bbox':b} for t,b,o in words]};d.update(extra);return d
def base():return [row('A','A 511',1,[('A',[10,10,20,20],0),('511',[24,10,40,20],0)]),row('B','B 511',1,[('B',[42,10,52,20],0),('511',[56,10,72,20],0)]),row('C','DOOR 19',1,[('DOOR',[220,10,250,20],0),('19',[254,10,270,20],0)])]
def test_parity_and_two_e_small_gap_navigation():
 from app.page_selector import _page_layout_columns
 x=base();b=s.build_layout_binding(x);p=b['page_layouts'][0]
 assert p['rendered_layout_lines']==_page_layout_columns([(i,i['raw_text']) for i in x],max_bytes=None)
 line=p['model_navigation'][0][0]
 assert line['evidence_ids']==['A','B'] and 'bbox' not in line
 assert line['parts']==[{'text':'A','evidence_id':'A'},{'text':'511','evidence_id':'A'},{'text':'B','evidence_id':'B'},{'text':'511','evidence_id':'B'}]
def test_ocr_and_single_column_are_not_layouts_but_are_explicit():
 b=s.build_layout_binding([row('OCR','x y',1,[('x',[1,1,2,2],0),('y',[4,1,5,2],0)],method='OCR'),row('ONE','a b',1,[('a',[1,1,2,2],0),('b',[4,1,5,2],0)])])
 assert b['sources'][0]['binding_reason']=='NOT_TEXT_LAYER' and b['sources'][1]['binding_status']=='BOUND' and not b['page_layouts']
 assert b['unbound_sources']==[{'evidence_id':'OCR','binding_reason':'NOT_TEXT_LAYER'}]
def test_same_bbox_keeps_original_input_order_e2_before_e10():
 x=[row('E2','two',1,[('two',[1,1,4,2],0)]),row('E10','ten',1,[('ten',[1,1,4,2],0)]),row('R','right',1,[('right',[200,1,210,2],0)])]
 assert s.build_layout_binding(x)['page_layouts'][0]['columns'][0][0]['spans'][0]['evidence_id']=='E2'
def test_documents_pages_emoji_and_invalid_span():
 x=[row('A','😀 one',1,[('one',[1,1,3,2],0)]),row('A2','left',1,[('left',[200,1,210,2],0)]),row('B','B two',1,[('B',[1,1,2,2],0)],'OTHER'),row('B2','right',1,[('right',[200,1,210,2],0)],'OTHER'),row('BAD','bad',2,[],text_map=[{'start':4,'end':5,'bbox':[1,1,2,2]}])]
 b=s.build_layout_binding(x);assert len(b['page_layouts'])==2 and b['page_layouts'][0]['columns'][0][0]['spans'][0]['start']==2 and b['sources'][4]['binding_reason']=='INVALID_TEXT_MAP'
def test_filter_reindexes_and_empty():
 x=base();b=s.build_layout_binding(x);f=s.filter_layout_binding(b,x,['E2'])
 assert f['sources'][0]['input_index']==1 and f['sources'][0]['evidence_id']=='B'
 assert s.filter_layout_binding(b,x,[])['selection_status']=='EMPTY'
@pytest.mark.parametrize('limit',[None,15,16,17,20,30])
def test_parameterized_legacy_renderer_byte_parity(limit):
 from app.page_selector import _page_layout_columns
 x=base();expected=_page_layout_columns([(i,i['raw_text']) for i in x],max_bytes=limit)
 actual=s.build_layout_binding(x,max_bytes=limit)['page_layouts']
 assert (actual[0]['rendered_layout_lines'] if actual else None)==expected
 if actual:
  assert [line['text'] for column in actual[0]['model_navigation'] for line in column]==[line for line in expected.splitlines() if line!='||']

def _replace_path(value,path,replacement):
 for key in path[:-1]:value=value[key]
 value[path[-1]]=replacement

@pytest.mark.parametrize(('path','replacement'),[
 (('sources',0,'evidence_id'),'UNRELATED'),
 (('sources',0,'document_id'),'OTHER_DOCUMENT'),
 (('sources',0,'page_number'),9),
 (('sources',0,'raw_text_sha256'),'0'*64),
 (('sources',0,'input_index'),99),
 (('sources',0,'unmapped_ranges'),[[0,1]]),
 (('page_layouts',0,'columns',0,0,'spans',0,'start'),1),
 (('page_layouts',0,'columns',0,0,'spans',0,'bbox'),[1,2,3,4]),
 (('page_layouts',0,'model_navigation',0,0,'evidence_ids'),['C']),
 (('page_layouts',0,'model_navigation',0,0,'parts',1,'evidence_id'),'C'),
 (('page_layouts',0,'model_navigation',0,0,'text'),'fabricated relationship'),
 (('page_layouts',0,'rendered_layout_lines'),'fabricated layout'),
 (('selection_status',),'EMPTY'),
])
def test_binding_tampering_fails_even_with_recomputed_object_hash(path,replacement):
 evidence=base();binding=s.build_layout_binding(evidence)
 _replace_path(binding,path,replacement)
 body={key:value for key,value in binding.items() if key!='binding_sha256'}
 binding['binding_sha256']=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 with pytest.raises(s.LayoutBindingError):s.verify_layout_binding(binding,evidence)

@pytest.mark.parametrize(('path','replacement'),[
 ((0,'raw_text'),'Z 511'),
 ((0,'evidence_id'),'CHANGED'),
 ((0,'document_id'),'OTHER_DOCUMENT'),
 ((0,'locator','page_number'),7),
 ((0,'text_map',0,'start'),1),
 ((0,'text_map',0,'bbox'),[12,10,22,20]),
 ((0,'extraction_method'),'OCR'),
])
def test_source_change_invalidates_existing_binding(path,replacement):
 evidence=base();binding=s.build_layout_binding(evidence);changed=copy.deepcopy(evidence)
 _replace_path(changed,path,replacement)
 with pytest.raises(s.LayoutBindingError):s.verify_layout_binding(binding,changed)

def test_unbound_member_raw_text_change_invalidates_complete_binding():
 evidence=[row('OCR','old non-navigation text',1,[('old',[1,1,2,2],0)],method='OCR'),*base()]
 binding=s.build_layout_binding(evidence)
 changed=copy.deepcopy(evidence);changed[0]['raw_text']='changed non-navigation text 😀'
 with pytest.raises(s.LayoutBindingError):s.verify_layout_binding(binding,changed)
 assert binding['sources'][0]['binding_status']=='UNBOUND'

def test_filter_verifies_before_rebuild_and_removes_all_dropped_provenance():
 evidence=base();binding=s.build_layout_binding(evidence)
 filtered=s.filter_layout_binding(binding,evidence,['E2','E3'])
 s.verify_layout_binding(filtered,evidence[1:])
 assert [item['input_index'] for item in filtered['sources']]==[1,2]
 assert [item['evidence_id'] for item in filtered['sources']]==['B','C']
 assert all(span['evidence_id'] in {'B','C'} for page in filtered['page_layouts'] for column in page['columns'] for line in column for span in line['spans'])
 altered=copy.deepcopy(filtered);altered['sources'][0]['evidence_id']='A'
 with pytest.raises(s.LayoutBindingError):s.verify_layout_binding(altered,evidence[1:])
 binding['sources'][0]['page_number']=99
 with pytest.raises(s.LayoutBindingError):s.filter_layout_binding(binding,evidence,[])

def test_filter_reorder_keeps_only_current_ordered_parts_without_dangling_ids():
 evidence=base();binding=s.build_layout_binding(evidence)
 filtered=s.filter_layout_binding(binding,evidence,['E3','E1'])
 assert [source['evidence_id'] for source in filtered['sources']]==['C','A']
 page=filtered['page_layouts'][0]
 current={source['evidence_id'] for source in filtered['sources']}
 assert [part['evidence_id'] for column in page['model_navigation'] for line in column for part in line['parts']]==['A','A','C','C']
 assert all(part['evidence_id'] in current for column in page['model_navigation'] for line in column for part in line['parts'])
 assert all(span['evidence_id'] in current for column in page['columns'] for line in column for span in line['spans'])
 s.verify_layout_binding(filtered,[evidence[2],evidence[0]])

@pytest.mark.parametrize('refs',[['E9'],['E1','E1']])
def test_filter_rejects_unknown_or_duplicate_members(refs):
 evidence=base()
 with pytest.raises(s.LayoutBindingError):s.filter_layout_binding(s.build_layout_binding(evidence),evidence,refs)

@pytest.mark.parametrize(('page','mapping','reason'),[
 (None,[],'MISSING_OR_INVALID_PAGE'),
 (True,[],'MISSING_OR_INVALID_PAGE'),
 (1,None,'MISSING_TEXT_MAP'),
 (1,[],'NO_NONBLANK_MAPPED_TOKEN'),
 (1,[{'start':0,'end':1,'bbox':[0,0,float('nan'),1]}],'INVALID_TEXT_MAP'),
 (1,[{'start':False,'end':1,'bbox':[0,0,1,1]}],'INVALID_TEXT_MAP'),
])
def test_unbound_source_keeps_its_original_text_and_explicit_gap(page,mapping,reason):
 evidence=[{'evidence_id':'A','document_id':'D','raw_text':'未绑定的完整原文😀',
            'extraction_method':'TEXT_LAYER','locator':{'page_number':page},'text_map':mapping}]
 original=copy.deepcopy(evidence);binding=s.build_layout_binding(evidence)
 assert evidence[0]['raw_text']==original[0]['raw_text']
 assert binding['sources'][0]['binding_status']=='UNBOUND'
 assert binding['sources'][0]['binding_reason']==reason
 assert binding['sources'][0]['unmapped_ranges']==[[0,len(evidence[0]['raw_text'])]]
 assert not binding['page_layouts']

@pytest.mark.parametrize('limit',[None,0,1,6,7,8,9,10,11,12,16,20,40])
def test_unicode_and_delimiter_legacy_byte_boundaries(limit):
 from app.page_selector import _page_layout_columns
 evidence=[row('A','蓝😀',1,[('蓝😀',[1,1,8,5],0)]),row('B','右列',1,[('右列',[200,1,212,5],0)])]
 expected=_page_layout_columns([(item,item['raw_text']) for item in evidence],max_bytes=limit)
 pages=s.build_layout_binding(evidence,max_bytes=limit)['page_layouts']
 assert (pages[0]['rendered_layout_lines'] if pages else None)==expected
 if pages:
  assert [line['text'] for column in pages[0]['model_navigation'] for line in column]==[line for line in expected.splitlines() if line!='||']

def test_unicode_parts_preserve_order_and_source_for_repeated_literals():
 text='蓝😀 511 511'
 evidence=[row('A',text,1,[('蓝😀',[1,1,8,5],0),('511',[12,1,20,5],3),('511',[24,1,32,5],7)]),row('B','右😀 511',1,[('右😀',[200,1,210,5],0),('511',[214,1,224,5],3)])]
 page=s.build_layout_binding(evidence)['page_layouts'][0]
 assert page['rendered_layout_lines']=='蓝😀 511 511\n||\n右😀 511'
 assert page['model_navigation'][0][0]['parts']==[{'text':'蓝😀','evidence_id':'A'},{'text':'511','evidence_id':'A'},{'text':'511','evidence_id':'A'}]
 assert page['model_navigation'][1][0]['parts']==[{'text':'右😀','evidence_id':'B'},{'text':'511','evidence_id':'B'}]
 assert [(span['start'],span['end']) for span in page['columns'][0][0]['spans']]==[(0,2),(3,6),(7,10)]

def test_default_binding_does_not_clip_long_source_or_layout():
 long_text='蓝😀'*22000
 evidence=[row('A',long_text,1,[(long_text,[1,1,8,5],0)]),row('B','RIGHT',1,[('RIGHT',[200,1,212,5],0)])]
 binding=s.build_layout_binding(evidence)
 assert binding['page_layouts'][0]['rendered_layout_lines']==long_text+'\n||\nRIGHT'
 assert binding['page_layouts'][0]['model_navigation'][0][0]['text']==long_text
 s.verify_layout_binding(binding,evidence)
