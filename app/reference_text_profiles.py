"""Closed, task-local DeepSeek text routes for new Reference evaluations."""
from __future__ import annotations

from app.db import DomainError
from app.settings import DEEPSEEK_BASE_URL

PROFILE_VERSION='reference-text-profile-1'
PROFILES={
    'FLASH_NONE': {'text_model':'deepseek-flash','inference_mode':'disabled','max_output_tokens':2600},
    'FLASH_LOW': {'text_model':'deepseek-flash','inference_mode':'thinking-low','max_output_tokens':8000},
    'PRO': {'text_model':'deepseek-v4-pro','inference_mode':'disabled','max_output_tokens':2600},
}

def profile(profile_id:str)->dict:
    if profile_id not in PROFILES:raise DomainError('Reference text profile is not supported.',400)
    route=PROFILES[profile_id]
    return {'provider':'deepseek','text_model':route['text_model'],'vision_enabled':False,
            'vision_model':None,'inference_mode':route['inference_mode'],
            'structured_output_mode':'json_object','api_protocol':'chat_completions',
            'profile_version':PROFILE_VERSION,'profile_id':profile_id,
            'max_output_tokens':route['max_output_tokens']}

def route(value:dict)->dict|None:
    if not isinstance(value,dict) or value.get('profile_version')!=PROFILE_VERSION:return None
    profile_id=value.get('profile_id')
    expected=profile_id and profile(profile_id)
    if value!=expected:raise DomainError('Frozen Reference text profile is invalid.',409)
    return expected

def require_channel(settings,value:dict)->dict|None:
    saved=route(value)
    if saved is None:return None
    if (settings.provider!='deepseek' or settings.api_base_url!=DEEPSEEK_BASE_URL
            or settings.api_protocol!='chat_completions' or settings.structured_output_mode!='json_object'
            or not settings.live_enabled or not settings.api_key or settings.live_errors()):
        raise DomainError('The official DeepSeek channel is not ready for this frozen Reference text profile.',409)
    return saved

def inference_parameters(route_value:dict)->dict:
    return ({'thinking':{'type':'enabled'},'reasoning_effort':'low'}
            if route_value['inference_mode']=='thinking-low' else {'thinking':{'type':'disabled'}})
