"""Finite portable opt64 contract prototype; no private authorization/paths.

The normal builder derives engine identity from its locked native source and
patch series. Existing three legacy profiles remain the default MTP0 family.
No model/binary selection is exposed through inference body fields.
"""
from dataclasses import dataclass
from common import DistributionError
import re

LEGACY_COMMIT='db3f8a7cdb33f1e88b68c6889331abd31098c923'
LATEST_COMMIT='13f7b540937dfafb33184aa0162a0e2d0fbfe65a'
OPT64='latest13f-defer-pc123-mtp2-opt64k'
PATCHES=('2e6a1a17c3c0a82ff0708c6e070ff69ac1b0f4a046cd8002cfcca868c73d075f',
 '5a63f673c72eb20b360059f37754f76b8c7814da6c350c1b9c988337bdd09f94',
 'eeeeeb30f92452a08eea37e1052f0ae1e67f84ece5ffcb84337e6ae569c1a534',
 'a84a99f1b70b177befa33842ade62894879ffa173395ac5149005c3ea08eccd9')
SOURCE_SHA='463795e9152d588f82f3fea27ef240a5f8798c2a4122820694b82294ffe27ce1'


@dataclass(frozen=True)
class Engine:
    engine_id:str
    source_commit:str
    patch_sha256:tuple[str,...]
    source_inventory_sha256:str
    binary_sha256:str
    default:bool=False


def from_generated_trust(record):
    """Only builder output binding exact recipe/source/binary admits OPT64."""
    keys={'engine_id','source_commit','patch_sha256','source_inventory_sha256','binary_sha256','default'}
    if not isinstance(record,dict) or set(record)!=keys:
        raise ValueError('portable_engine_trust_shape')
    if (record['engine_id']!=OPT64 or record['source_commit']!=LATEST_COMMIT
        or record['patch_sha256']!=list(PATCHES) or record['source_inventory_sha256']!=SOURCE_SHA
        or record['default'] is not False or not re.fullmatch('[0-9a-f]{64}',str(record['binary_sha256']))):
        raise ValueError('portable_engine_not_exact_opt64_recipe')
    return Engine(OPT64,LATEST_COMMIT,PATCHES,SOURCE_SHA,record['binary_sha256'])


def latest_argv(engine,profile,binary,model,port,cache):
    """Internal paths supplied by normal verified installation, never request."""
    if engine.engine_id!=OPT64 or profile.profile_id!='ctx64k-mtp2' or profile.context_tokens!=65536:
        raise ValueError('portable_opt_engine_context_out_of_scope')
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('portable_opt_engine_port')
    return [str(binary),'--model',str(model),'--bind','127.0.0.1:'+str(port),
        '--max-seq','65536','--mtp-drafts','2','--max-batch','1','--kv-cache','bf16',
        '--cache-bytes','8589934592','--max-sessions','1','--disk-cache-dir',str(cache),
        '--disk-cache-bytes','0','--ngram-table','paged','--ngram-preload','true',
        '--pin-weights','off','--pin-hold','1m','--thinking','true','--thinking-budget','off',
        '--thinking-nudges','false','--tool-call-ends-thinking','false','--idle-unload','0','--queue','4']


def native_policy_matches(engine,profile,state):
    if engine.engine_id!=OPT64 or profile.profile_id!='ctx64k-mtp2' or profile.context_tokens!=65536:return False
    return (state.get('proof_valid') is True and state.get('binary_sha256')==engine.binary_sha256
      and state.get('native_engine_effective')=={'context_tokens':65536,'kv_cache':'bf16','mtp_drafts':2,'max_batch':1}
      and all(type(state['native_engine_effective'].get(key)) is int for key in ('context_tokens','mtp_drafts','max_batch'))
      and state.get('qsa_route_effective')=={'requested':'split','route':'split'})


def effective_env(base):
    # Normal runner already constructs a closed, nonsecret HOME/PATH/TMP/LANG.
    if set(base)!={'HOME','PATH','TMPDIR','LANG'}:raise ValueError('portable_engine_environment_not_closed')
    return {**base,'LILY_QSA_ROUTE':'split','LILY_QSA_SCORES':'scalar'}


def generated_record(recipe, binary_sha256):
    record={'engine_id':OPT64,'source_commit':recipe['source_commit'],'patch_sha256':recipe['patch_sha256'],
            'source_inventory_sha256':recipe['source_inventory_sha256'],'binary_sha256':binary_sha256,'default':False}
    from_generated_trust(record)
    return record


def validate_engine_records(trust, pinned):
    records=trust.get('optin_engines', {})
    enabled=trust.get('enabled_optin_profiles', [])
    if (not isinstance(records,dict) or set(records) not in (set(),{OPT64})
        or not isinstance(enabled,list) or enabled not in ([],['ctx64k-mtp2'])
        or bool(enabled)!=bool(records)):
        raise DistributionError('generated opt-in engine authorization differs')
    if not records:return {}
    from native_recipe import select_lily_recipe
    select_lily_recipe(pinned,OPT64)
    try:return {OPT64:from_generated_trust(records[OPT64])}
    except ValueError:raise DistributionError('generated opt-in engine identity differs') from None


def engine_for(trusted, profile):
    if profile.engine_id == 'legacy-db3-mtp0':return None
    engine=trusted.get('engines',{}).get(OPT64)
    if profile.engine_id != OPT64 or profile.profile_id!='ctx64k-mtp2' or not isinstance(engine,Engine):
        raise DistributionError('selected opt-in engine is not generated and trusted')
    return engine


def record_fields(engine):
    return {} if engine is None else {'engine_id':engine.engine_id,'engine_source_commit':engine.source_commit,
                                      'engine_source_inventory_sha256':engine.source_inventory_sha256}


def native_matches(record, *, require_dispatch=False):
    value=record.get('native_engine_effective')
    if (record.get('proof_valid') is not True or not isinstance(value,dict)
        or value != {'context_tokens':65536,'kv_cache':'bf16','mtp_drafts':2,'max_batch':1}
        or any(type(value.get(k)) is not int for k in ('context_tokens','mtp_drafts','max_batch'))
        or record.get('native_context_tokens')!=65536
        or record.get('qsa_route_effective')!={'requested':'split','route':'split'}):return False
    if not require_dispatch:return True
    dispatch=record.get('qsa_dispatch_metadata')
    return (isinstance(dispatch,dict) and type(dispatch.get('sparse_prefill_rows')) is int
        and 16<=dispatch['sparse_prefill_rows']<=65536 and dispatch.get('route')=='split'
        and type(dispatch.get('split_dispatch_count')) is int and dispatch['split_dispatch_count']==1
        and type(dispatch.get('query_dispatch_count')) is int and dispatch['query_dispatch_count']==0)
