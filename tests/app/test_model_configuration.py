"""Customer model settings are local, fail closed, and never expose credentials."""
import pytest

from app.model_configuration import (configured_settings, load_active_configuration,
                                     public_configuration, save_active_configuration)
from app.settings import Settings
from .conftest import upload


def test_custom_loopback_profile_round_trips_without_key(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    configured=configured_settings(
        base,provider='custom',api_base_url='http://127.0.0.1:11434/v1',
        model='qwen3:8b',vision_enabled=True,structured_output_mode='json_schema')
    assert configured.is_local_model() and configured.live_errors()==[]
    assert configured.vision_enabled and configured.vision_model=='qwen3:8b'
    assert configured.structured_output_mode=='json_schema'
    save_active_configuration(configured)
    loaded=load_active_configuration(base)
    assert loaded is not None and loaded.provider==configured.provider
    assert loaded.api_base_url=='http://127.0.0.1:11434/v1'
    assert loaded.cheap_model=='qwen3:8b' and loaded.api_key==''
    assert loaded.vision_enabled and loaded.vision_model=='qwen3:8b'
    assert loaded.structured_output_mode=='json_schema'
    assert loaded.api_protocol=='chat_completions'
    assert public_configuration(loaded)['vision_enabled'] is True
    assert public_configuration(loaded)['structured_output_mode']=='json_schema'
    assert public_configuration(loaded)['api_protocol']=='chat_completions'


def test_presets_reject_custom_native_schema_mode(tmp_path):
    for provider in ('mock','deepseek','gemini'):
        with pytest.raises(ValueError,match='only for OpenAI or a custom'):
            configured_settings(
                Settings(tmp_path,start_worker=False),provider=provider,
                api_key='synthetic-valid-key',structured_output_mode='json_schema')


def test_openai_profile_uses_official_stateless_responses_route(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    configured=configured_settings(
        base,provider='openai',model='gpt-test-model',
        api_key='synthetic-valid-key',vision_enabled=True,
        structured_output_mode='json_schema')

    assert configured.provider=='openai'
    assert configured.api_base_url=='https://api.openai.com/v1'
    assert configured.cheap_model==configured.vision_model=='gpt-test-model'
    assert configured.api_protocol=='responses'
    assert configured.inference_mode()=='provider default'
    assert configured.live_errors()==[]
    save_active_configuration(configured)
    loaded=load_active_configuration(base)
    assert loaded is not None and loaded.api_protocol=='responses'
    assert public_configuration(loaded)=={
        'provider':'openai','provider_identity':'openai',
        'api_base_url':'https://api.openai.com/v1','model':'gpt-test-model',
        'local_model':False,'saved_key':True,'saved_profile':True,
        'vision_enabled':True,'structured_output_mode':'json_schema',
        'api_protocol':'responses','reasoning_effort':'none',
    }


def test_remote_custom_profile_requires_https_key_and_valid_model(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    for model,values in (
        ('remote-model',{'api_base_url':'http://remote.example/v1','api_key':'synthetic-valid-key'}),
        ('remote-model',{'api_base_url':'https://remote.example/v1','api_key':''}),
        ('bad model',{'api_base_url':'https://remote.example/v1','api_key':'synthetic-valid-key'}),
    ):
        try:
            configured_settings(base,provider='custom',model=model,**values)
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe remote configuration was accepted')


def test_public_model_configuration_never_contains_key(tmp_path):
    configured=configured_settings(
        Settings(tmp_path,start_worker=False),provider='deepseek',
        api_key='synthetic-valid-key')
    public=public_configuration(configured)
    assert public['provider']=='deepseek' and public['model']=='deepseek-flash'
    assert public['structured_output_mode']=='json_object'
    assert public['api_protocol']=='chat_completions'
    assert 'input_rate' not in public and 'output_rate' not in public
    assert 'api_key' not in public and 'synthetic-valid-key' not in repr(public)


def test_deepseek_selected_page_images_are_explicitly_toggleable(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    text_only=configured_settings(
        base,provider='deepseek',api_key='synthetic-valid-key',vision_enabled=False)
    multimodal=configured_settings(
        base,provider='deepseek',api_key='synthetic-valid-key',vision_enabled=True)

    assert text_only.cheap_model=='deepseek-flash'
    assert text_only.vision_enabled is False
    assert public_configuration(text_only)['vision_enabled'] is False
    assert multimodal.vision_enabled is True
    assert multimodal.vision_model=='deepseek-flash'


def test_deepseek_legacy_flash_alias_loads_as_current_flash_without_a_call(tmp_path):
    configured=configured_settings(
        Settings(tmp_path,start_worker=False),provider='deepseek',model='deepseek-v4-flash',
        api_key='synthetic-valid-key')
    assert configured.cheap_model=='deepseek-flash'
    assert configured.live_errors()==[]


def test_deepseek_pro_is_an_independent_text_profile_without_a_call(tmp_path):
    configured=configured_settings(
        Settings(tmp_path,start_worker=False),provider='deepseek',model='deepseek-v4-pro',
        api_key='synthetic-valid-key',vision_enabled=False)
    assert configured.cheap_model=='deepseek-v4-pro'
    assert configured.vision_enabled is False
    assert configured.live_errors()==[]


def test_deepseek_reference_thinking_profile_round_trips_without_a_call(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    configured=configured_settings(
        base,provider='deepseek',model='deepseek-flash',
        api_key='synthetic-valid-key',reasoning_effort='low')

    assert configured.reference_reasoning_effort=='low'
    assert configured.reference_inference_parameters()=={
        'thinking':{'type':'enabled'},'reasoning_effort':'low'}
    assert configured.inference_mode()=='thinking-low'
    save_active_configuration(configured)
    loaded=load_active_configuration(base)
    assert loaded is not None and loaded.reference_reasoning_effort=='low'
    assert public_configuration(loaded)['reasoning_effort']=='low'


def test_reference_thinking_is_rejected_for_non_deepseek_profiles(tmp_path):
    with pytest.raises(ValueError,match='only for DeepSeek'):
        configured_settings(
            Settings(tmp_path,start_worker=False),provider='custom',
            api_base_url='http://127.0.0.1:11434/v1',model='qwen3:8b',
            reasoning_effort='low')


def test_deepseek_pro_rejects_image_input_before_http(tmp_path):
    with pytest.raises(ValueError,match='image-capable Flash'):
        configured_settings(
            Settings(tmp_path,start_worker=False),provider='deepseek',model='deepseek-v4-pro',
            api_key='synthetic-valid-key',vision_enabled=True)


def test_deepseek_rejects_unlisted_model_before_http(tmp_path):
    with pytest.raises(ValueError,match='requires DeepSeek V4.1 Flash or V4 Pro'):
        configured_settings(
            Settings(tmp_path,start_worker=False),provider='deepseek',model='deepseek-v4.1-pro',
            api_key='synthetic-valid-key')


def test_model_settings_endpoint_applies_local_profile_without_model_call(client):
    selected=[];client.app.state.request_model_restart=selected.append
    before=client.get('/api/model-settings').json()
    assert before['provider']=='mock' and before['restart_supported']
    response=client.post('/api/model-settings',json={
        'provider':'custom','api_base_url':'http://127.0.0.1:11434/v1','model':'qwen3:8b',
        'api_key':'','vision_enabled':True,'structured_output_mode':'json_schema',
        'remember':True,'approved':True})
    assert response.status_code==202 and response.json()['status']=='RESTARTING'
    assert len(selected)==1 and selected[0].is_local_model() and selected[0].vision_enabled
    assert selected[0].structured_output_mode=='json_schema'
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]
    assert (client.app.state.settings.data_dir/'credentials/active-model-profile.json').exists()
    assert 'api_key' not in response.text and '127.0.0.1:11434' not in response.text


def test_model_settings_endpoint_accepts_a_replacement_key_without_echo(client):
    selected=[];client.app.state.request_model_restart=selected.append
    replacement='synthetic-replacement-key-for-offline-test'
    response=client.post('/api/model-settings',json={
        'provider':'deepseek','api_key':replacement,
        'remember':False,'approved':True})
    assert response.status_code==202 and len(selected)==1
    assert selected[0].api_key==replacement
    assert replacement not in response.text and 'api_key' not in response.text
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]


def test_model_settings_endpoint_applies_bounded_deepseek_thinking_without_call(client):
    selected=[];client.app.state.request_model_restart=selected.append
    response=client.post('/api/model-settings',json={
        'provider':'deepseek','api_key':'synthetic-valid-key-for-offline-test',
        'reasoning_effort':'low','remember':False,'approved':True})

    assert response.status_code==202 and len(selected)==1
    assert selected[0].reference_reasoning_effort=='low'
    assert selected[0].reference_inference_parameters()['reasoning_effort']=='low'
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]


def test_model_settings_endpoint_accepts_openai_without_calling_it_or_echoing_key(client):
    selected=[];client.app.state.request_model_restart=selected.append
    key='synthetic-openai-key-for-offline-test'
    response=client.post('/api/model-settings',json={
        'provider':'openai','model':'gpt-test-model','api_key':key,
        'vision_enabled':True,'structured_output_mode':'json_schema',
        'remember':False,'approved':True})
    assert response.status_code==202 and len(selected)==1
    assert selected[0].provider=='openai' and selected[0].api_protocol=='responses'
    assert selected[0].vision_model=='gpt-test-model'
    assert key not in response.text and 'api_key' not in response.text
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]


def test_model_settings_endpoint_does_not_echo_rejected_key(client):
    client.app.state.request_model_restart=lambda value:None
    response=client.post('/api/model-settings',json={
        'provider':'deepseek','api_key':'short secret',
        'remember':False,'approved':True})
    assert response.status_code==400 and 'short secret' not in response.text
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]


def test_model_settings_storage_error_does_not_expose_local_path(client,monkeypatch):
    client.app.state.request_model_restart=lambda value:None
    private_path=r'C:\private\credentials\provider.dpapi'
    monkeypatch.setattr('app.main.save_active_configuration',
                        lambda value:(_ for _ in ()).throw(OSError(private_path)))
    response=client.post('/api/model-settings',json={
        'provider':'custom','api_base_url':'http://127.0.0.1:11434/v1','model':'qwen3:8b',
        'remember':True,'approved':True})
    assert response.status_code==400 and private_path not in response.text
    assert response.json()['detail']=='The model settings could not be saved locally.'


def test_model_settings_change_is_blocked_by_active_analysis(client,project):
    upload(client,project['id'],'input.txt',b'plain project text')
    created=client.post(f'/api/projects/{project["id"]}/analysis-runs')
    assert created.status_code==202
    client.app.state.request_model_restart=lambda value:None
    response=client.post('/api/model-settings',json={'provider':'mock'})
    assert response.status_code==409 and 'active analysis' in response.text
