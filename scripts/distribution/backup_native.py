"""Private logical database backup and isolated restoration for portable installations."""
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from common import DistributionError,no_symlinks,private_directory,read_object,write_new
from trust import ALIAS,validate,private_file,installation,atomic_private_json,verified_file
from agent import postgres_tools,pg_environment,private_run,pg_start,pg_stop,mutation,preflight_ports


def _verify_recovery_source(trusted):
    required={'scripts/distribution/backup_native.py','scripts/distribution/liliuxflow.py'}
    records={item['path']:item for item in trusted['trust']['source_files']}
    if not required.issubset(records):raise DistributionError('recovery source is not bound to reviewed runtime trust')
    for name in required:verified_file(trusted['source_root'],records[name])


def backup(data,output):
    trusted=validate(data,require_checkpoint=False);_verify_recovery_source(trusted);cfg=trusted['config'];output=no_symlinks(output)
    private_directory(output.parent)
    if output.exists():raise DistributionError('backup output already exists')
    if not cfg['database_initialized']:raise DistributionError('use bootstrap backup before database initialization')
    # An offline database must be started under own mutation lease. A running own agent permits pg_dump's MVCC snapshot.
    state=data/'run/agent.json';own_pg=None
    if not state.exists():
        with mutation(data):
            preflight_ports(cfg['ports']);own_pg=pg_start(trusted)
            try:return _dump(trusted,output)
            finally:pg_stop(trusted,own_pg)
    record=read_object(private_file(state))
    from ownership import unchanged
    if record.get('installation_id')!=cfg['installation_id'] or not unchanged(record['agent']) or not unchanged(record['postgres']):
        raise DistributionError('live backup process ownership differs')
    return _dump(trusted,output)


def _dump(trusted,output):
    data=trusted['data_root'];cfg=trusted['config'];pg=postgres_tools(trusted)
    with tempfile.TemporaryDirectory(prefix='backup-',dir=data/'backups') as tmp:
        task=Path(tmp);dump=task/'litellm.dump'
        private_run([pg/'pg_dump','-h','127.0.0.1','-p',cfg['ports']['postgresql'],'-U','litellm_app','-d','litellm','--format=custom','--no-owner','--file',dump],env=pg_environment(trusted,owner=False),timeout=300)
        descriptor=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as raw,tarfile.open(fileobj=raw,mode='w:gz') as archive:
            files=[(dump,'database/litellm.dump'),(data/'install.json','installation/install.json'),(data/'secrets/bootstrap.json','secrets/bootstrap.json')]
            if (data/'secrets/caller.json').exists():files.append((data/'secrets/caller.json','secrets/caller.json'))
            for file,name in files:
                no_symlinks(file);info=archive.gettarinfo(str(file),arcname=name);info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o600
                with file.open('rb') as handle:archive.addfile(info,handle)
    return {'state':'backed_up','scope':'own database logical snapshot, installation settings, bootstrap/caller credentials',
            'encrypted':False,'mode':'0600','weights_included':False,'cache_included':False}


def restore(data,archive):
    """Restore into a newly initialized empty own DB only; never overwrite existing user data."""
    trusted=validate(data,require_checkpoint=False);_verify_recovery_source(trusted);cfg=trusted['config'];pg=postgres_tools(trusted)
    if not cfg['database_initialized']:raise DistributionError('initialize a new isolated installation before restore')
    if (data/'run/agent.json').exists():raise DistributionError('stop this installation before isolated restore')
    archive=private_file(archive)
    with mutation(data),tempfile.TemporaryDirectory(prefix='restore-',dir=data/'backups') as tmp:
        task=Path(tmp);dump=task/'litellm.dump';saved={}
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers();allowed={'database/litellm.dump','installation/install.json','secrets/bootstrap.json','secrets/caller.json'}
            if {m.name for m in members}-allowed or len({m.name for m in members})!=len(members) or sum(m.size for m in members)>2*1024**3 or any(not m.isfile() or m.size>2*1024**3 for m in members):
                raise DistributionError('backup archive contains unsafe or excessive members')
            for member in members:
                stream=tar.extractfile(member)
                if member.name=='database/litellm.dump':
                    with dump.open('xb') as out:shutil.copyfileobj(stream,out)
                else:
                    if member.size>1024**2:raise DistributionError('backup metadata exceeds cap')
                    saved[member.name]=json.loads(stream.read())
        if not dump.exists() or 'secrets/bootstrap.json' not in saved or 'installation/install.json' not in saved:raise DistributionError('backup is incomplete')
        if saved['installation/install.json'].get('installation_id')==cfg['installation_id']:
            raise DistributionError('restore requires an independently initialized target identity')
        incoming=saved['secrets/bootstrap.json']
        required=('LITELLM_MASTER_KEY','LITELLM_SALT_KEY','UI_PASSWORD','MANAGER_BACKEND_TOKEN','GUARD_CONTROL_TOKEN')
        if not isinstance(incoming,dict) or any(not isinstance(incoming.get(k),str) or len(incoming[k])<24 for k in required) or not isinstance(incoming.get('UI_USERNAME'),str):
            raise DistributionError('backup credentials are incomplete; no database modified')
        caller=saved.get('secrets/caller.json')
        if caller is not None and (not isinstance(caller,dict) or not isinstance(caller.get('api_key'),str) or caller.get('alias')!=ALIAS):
            raise DistributionError('backup caller identity is invalid; no database modified')
        # Salt/master must match restored encrypted provider keys, while PG owner password and data path remain this installation's.
        incoming=saved['secrets/bootstrap.json'];secret=trusted['secrets']
        identity=pg_start(trusted)
        try:
            base=[pg/'psql','-h','127.0.0.1','-p',cfg['ports']['postgresql'],'-U','litellm_app','-d','litellm','-At','-v','ON_ERROR_STOP=1']
            tables=private_run(base,env=pg_environment(trusted,owner=False),input_text="SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;\n").splitlines()
            import re
            for table in tables:
                if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',table):raise DistributionError('restore target has an unknown table identifier')
                if table=='_prisma_migrations':continue
                count=private_run(base,env=pg_environment(trusted,owner=False),input_text='SELECT count(*) FROM "'+table+'";\n').strip()
                if count!='0':raise DistributionError('restore target contains application records; all existing data preserved')
            # Existing empty schema is dropped only in this independently initialized installation, explicitly requested by restore.
            private_run([pg/'psql','-h','127.0.0.1','-p',cfg['ports']['postgresql'],'-U','litellm_app','-d','litellm','-v','ON_ERROR_STOP=1'],env=pg_environment(trusted,owner=False),input_text='DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION litellm_app;\n')
            private_run([pg/'pg_restore','--exit-on-error','--no-owner','--no-privileges','-h','127.0.0.1','-p',cfg['ports']['postgresql'],'-U','litellm_app','-d','litellm',dump],env=pg_environment(trusted,owner=False),timeout=300)
            for k in ('LITELLM_MASTER_KEY','LITELLM_SALT_KEY','UI_USERNAME','UI_PASSWORD','MANAGER_BACKEND_TOKEN','GUARD_CONTROL_TOKEN'):secret[k]=incoming[k]
            atomic_private_json(data/'secrets/bootstrap.json',secret)
            caller=saved.get('secrets/caller.json')
            if caller:
                caller['api_base']='http://127.0.0.1:'+str(cfg['ports']['litellm'])+'/v1';caller['compat_base']='http://127.0.0.1:'+str(cfg['ports']['compat'])
                atomic_private_json(data/'secrets/caller.json',caller);cfg['caller_key_created']=True;atomic_private_json(data/'install.json',cfg)
        finally:pg_stop(trusted,identity)
    return {'state':'restored','target':'new empty isolated database','weights_preserved':True,'source_database_untouched':True}
