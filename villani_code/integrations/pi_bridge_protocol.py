from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal
import json
PROTOCOL_VERSION=1
BridgeMode=Literal['runner','villani']
@dataclass(slots=True)
class BridgeConfig:
    provider:str|None=None; model:str|None=None; base_url:str|None=None; api_key:str|None=None; pi_model_proxy:bool=False
@dataclass(slots=True)
class BridgeLimits: max_turns:int|None=None
@dataclass(slots=True)
class RunCommand:
    id:str; task:str; repo:str; mode:BridgeMode='runner'; config:BridgeConfig=field(default_factory=BridgeConfig); limits:BridgeLimits=field(default_factory=BridgeLimits)
@dataclass(slots=True)
class ApprovalResponseCommand:
    id:str; request_id:str; approved:bool

def to_json_line(event:dict[str,Any])->str: return json.dumps(event,separators=(',',':'))+'\n'
def parse_json_line(line:str)->dict[str,Any]:
    v=json.loads(line)
    if not isinstance(v,dict): raise ValueError('JSON command must be an object')
    return v
def _nonempty(p,k):
    v=p.get(k)
    if not isinstance(v,str) or not v.strip(): raise ValueError(f'{k} is required')
    return v
def parse_run_command(payload:dict[str,Any])->RunCommand:
    rid=_nonempty(payload,'id'); task=_nonempty(payload,'task'); repo=_nonempty(payload,'repo')
    mode=payload.get('mode','runner')
    if mode not in {'runner','villani'}: raise ValueError("mode must be 'runner' or 'villani'")
    c=payload.get('config') or {}; l=payload.get('limits') or {}
    if not isinstance(c,dict) or not isinstance(l,dict): raise ValueError('config and limits must be objects')
    max_turns=l.get('max_turns')
    if max_turns is not None and not isinstance(max_turns,int): raise ValueError('max_turns must be an integer')
    return RunCommand(rid,task,repo,mode,BridgeConfig(provider=c.get('provider'),model=c.get('model'),base_url=c.get('base_url'),api_key=c.get('api_key'),pi_model_proxy=bool(c.get('pi_model_proxy',False))),BridgeLimits(max_turns=max_turns))
def parse_approval_response_command(payload:dict[str,Any])->ApprovalResponseCommand:
    rid=_nonempty(payload,'id'); req=_nonempty(payload,'request_id')
    if not isinstance(payload.get('approved'),bool): raise ValueError('approved must be boolean')
    return ApprovalResponseCommand(rid,req,payload['approved'])
def ready_event()->dict[str,Any]: return {'type':'ready','protocol_version':PROTOCOL_VERSION}
