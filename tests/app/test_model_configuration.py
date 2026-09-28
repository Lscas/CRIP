"""Customer model settings are local, fail closed, and never expose credentials."""
from decimal import Decimal

from app.model_configuration import (configured_settings, load_active_configuration,
                                     public_configuration, save_active_configuration)
from app.settings import Settings
from .conftest import upload


def test_custom_loopback_profile_round_trips_without_key(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    configured=configured_settings(
        base,provider='custom',api_base_url='http://127.0.0.1:11434/v1',
        model='qwen3:8b',input_rate='0',output_rate='0')
    assert configured.is_local_model() and configured.live_errors()==[]
    save_active_configuration(configured)
    loaded=load_active_configuration(base)
    assert loaded is not None and loaded.provider==configured.provider
    assert loaded.api_base_url=='http://127.0.0.1:11434/v1'
    assert loaded.cheap_model=='qwen3:8b' and loaded.api_key==''


def test_remote_custom_profile_requires_https_key_and_positive_rates(tmp_path):
    base=Settings(tmp_path,start_worker=False)
    for values in (
        {'api_base_url':'http://remote.example/v1','api_key':'synthetic-valid-key','input_rate':'1','output_rate':'2'},
        {'api_base_url':'https://remote.example/v1','api_key':'','input_rate':'1','output_rate':'2'},
        {'api_base_url':'https://remote.example/v1','api_key':'synthetic-valid-key','input_rate':'0','output_rate':'2'},
    ):
        try:
            configured_settings(base,provider='custom',model='remote-model',**values)
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe remote configuration was accepted')


def test_public_model_configuration_never_contains_key(tmp_path):
    configured=configured_settings(
        Settings(tmp_path,start_worker=False),provider='deepseek',
        api_key='synthetic-valid-key',input_rate=Decimal('4.4'),output_rate=Decimal('13.2'))
    public=public_configuration(configured)
    assert public['provider']=='deepseek' and public['model']=='deepseek-v4-flash'
    assert 'api_key' not in public and 'synthetic-valid-key' not in repr(public)


def test_model_settings_endpoint_applies_local_profile_without_model_call(client):
    selected=[];client.app.state.request_model_restart=selected.append
    before=client.get('/api/model-settings').json()
    assert before['provider']=='mock' and before['restart_supported']
    response=client.post('/api/model-settings',json={
        'provider':'custom','api_base_url':'http://127.0.0.1:11434/v1','model':'qwen3:8b',
        'api_key':'','input_rate':'0','output_rate':'0','remember':True,'approved':True})
    assert response.status_code==202 and response.json()['status']=='RESTARTING'
    assert len(selected)==1 and selected[0].is_local_model()
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]
    assert (client.app.state.settings.data_dir/'credentials/active-model-profile.json').exists()
    assert 'api_key' not in response.text and '127.0.0.1:11434' not in response.text


def test_model_settings_endpoint_accepts_a_replacement_key_without_echo(client):
    selected=[];client.app.state.request_model_restart=selected.append
    replacement='synthetic-replacement-key-for-offline-test'
    response=client.post('/api/model-settings',json={
        'provider':'deepseek','api_key':replacement,'input_rate':'4.4','output_rate':'13.2',
        'remember':False,'approved':True})
    assert response.status_code==202 and len(selected)==1
    assert selected[0].api_key==replacement
    assert replacement not in response.text and 'api_key' not in response.text
    assert client.app.state.db.all('SELECT * FROM model_calls')==[]


def test_model_settings_endpoint_does_not_echo_rejected_key(client):
    client.app.state.request_model_restart=lambda value:None
    response=client.post('/api/model-settings',json={
        'provider':'deepseek','api_key':'short secret','input_rate':'4.4','output_rate':'13.2',
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
        'input_rate':'0','output_rate':'0','remember':True,'approved':True})
    assert response.status_code==400 and private_path not in response.text
    assert response.json()['detail']=='The model settings could not be saved locally.'


def test_model_settings_change_is_blocked_by_active_analysis(client,project):
    upload(client,project['id'],'input.txt',b'plain project text')
    created=client.post(f'/api/projects/{project["id"]}/analysis-runs')
    assert created.status_code==202
    client.app.state.request_model_restart=lambda value:None
    response=client.post('/api/model-settings',json={'provider':'mock'})
    assert response.status_code==409 and 'active analysis' in response.text
