"""Stable, source-text-free named Reference input commitments."""
from __future__ import annotations
import hashlib,json,re
from app.db import DomainError
from app.reference_text_profiles import profile as canonical_profile

VERSION='reference-input-commitment-1'


def require_ledger_profile(call:dict,profile_id:str|None)->None:
    """Reject profile downgrades using the durable call ledger as authority."""
    version=call.get('reference_input_commitment_version')
    digest=call.get('reference_input_commitment_sha256')
    if (version is None)!=(digest is None):
        raise DomainError('Reference call commitment columns are inconsistent.',409)
    if version is None:
        if profile_id is not None:
            raise DomainError('Named Reference profile has no settled input commitment.',409)
        return
    if version!=VERSION or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise DomainError('Reference call input commitment is malformed.',409)
    if profile_id is None:
        raise DomainError('A committed Reference call requires its explicit named execution profile.',409)

def sha256(profile:dict,receipt:dict)->str:
    profile_id=profile.get('profile_id') if isinstance(profile,dict) else None
    if not isinstance(profile_id,str):raise DomainError('Named Reference commitment requires a profile id.',409)
    canonical=canonical_profile(profile_id)
    if profile!=canonical:raise DomainError('Named Reference commitment profile is not canonical.',409)
    if not isinstance(receipt,dict):raise DomainError('Named Reference commitment receipt is invalid.',409)
    value={'commitment_version':VERSION,'execution_profile':canonical,
           'receipt':{key:value for key,value in receipt.items() if key!='cached'}}
    try:raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError) as exc:raise DomainError('Named Reference commitment is not serializable.',409) from exc
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
