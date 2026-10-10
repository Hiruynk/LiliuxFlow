# SPDX-License-Identifier: Apache-2.0
"""Bounded private numeric stderr evidence; never native release authority."""
from __future__ import annotations
import hashlib, json, os, queue, re, stat, threading
from pathlib import Path
from common import DistributionError, no_symlinks, private_directory

MAX_LINE=4096
CHUNK=65536
MAX_BYTES=4*1024**2
QUEUE_LIMIT=512
UINT=(1<<64)-1
_ISSUER=object()
_ID=re.compile(r'req-(0|[1-9][0-9]{0,19})-(0|[1-9][0-9]{0,19})')
_RESPONSE=re.compile(r'(?:chatcmpl|cmpl)-(0|[1-9][0-9]{0,19})-(0|[1-9][0-9]{0,19})')
_HEX=re.compile('[0-9a-f]{64}')
_ENUM={'clock':{'mach_absolute_time_ns':1},'kv_cache':{'bf16':1},
       'profile_id':{'ctx64k-mtp2':0,'ctx128k-mtp2':1,'ctx262k-mtp2':2},
       'step_mode':{'initial_draw':0,'normal':1,'thinking_control':2},
       'reason':{'first_trunk_draw':0,'complete_quantum':1,'physical_context_scratch_reserve':2},
       'requested':{'split':2},'route':{'split':2}}
_LAYOUT={
 'resident':'protocol resident_context_tokens kv_cache mtp_drafts max_batch weight_load_generation weight_load_count weight_allocation_id weight_buffer_count weight_allocation_bytes paged_weight_bytes',
 'engine':'context_tokens kv_cache mtp_drafts max_batch',
 'route':'requested route',
 'queue_enter':'request_id phase',
 'lane_acquired':'request_id phase queue_wait_ms',
 'lane_released':'request_id phase cancelled lane_ms',
 'mtp2_policy':'request_id phase protocol clock admitted_ns profile_id context_tokens prompt_tokens output_tokens resident_context_tokens weight_load_generation weight_load_count weight_allocation_id mtp_drafts',
 'mtp2_step':'request_id phase protocol clock produced_ns settled_ns resident_epoch step_id step_index step_mode draft_count draft_tokens_proposed draft_tokens_accepted generated_token_start generated_token_count generated_tokens delivered_token_count cumulative_delivered cumulative_drafted cumulative_accepted position',
 'initial':'request_id native_request_id response_id phase protocol clock produced_ns settled_ns resident_epoch step_id step_index step_mode reason draft_count draft_tokens_proposed draft_tokens_accepted generated_token_start generated_token_count generated_tokens delivered_token_count cumulative_delivered cumulative_drafted cumulative_accepted position',
 'mtp2_released':'request_id phase clock released_ns resident_epoch cancelled',
 'mtp2_context_tail':'request_id phase reason reserve_tokens context_tail_tokens start_position end_position generated_tokens resident_context_tokens mtp_drafts',
 'mtp2_reservation':'request_id phase reservation_bytes allocated_session_bytes allocated_capacity scratch_bytes',
 'mtp2_continuation_budget':'request_id phase logical_bytes allocated_bytes reserved_bytes',
 'mtp2_yield':'request_id phase resident_epoch generated_tokens position reason',
 'prefill_progress':'request_id phase completed_chunks prefilled_tokens'}
_LAYOUT={k:tuple(v.split()) for k,v in _LAYOUT.items()}
_PREFIX={'LILY_MTP2_RESIDENT':'resident','LILY_ENGINE_EFFECTIVE':'engine','LILY_QSA_ROUTE_EFFECTIVE':'route'}

class _Numeric:
    __slots__=('fields','issuer')
    def __init__(self,fields):self.fields=tuple(fields.items());self.issuer=_ISSUER

def parse_line(line):
    """Whole-line, exact-order, ASCII grammar; no raw text is returned."""
    if type(line) is not bytes or len(line)>MAX_LINE:return None
    try:
        text=line.decode('ascii')
        if not text or '\n' in text or '\r' in text:return None
        tokens=text.split(' ');prefix=tokens.pop(0);items=[]
        for token in tokens:
            key,value=token.split('=',1);items.append((key,value))
        keys=tuple(k for k,v in items);values=dict(items)
        phase=_PREFIX.get(prefix)
        if prefix=='[lily-meta]':phase=values.get('phase')
        if phase=='mtp2_step' and 'native_request_id' in values:layout='initial'
        else:layout=phase
        if layout not in _LAYOUT or keys!=_LAYOUT[layout]:return None
        result={'phase':phase}
        for key,value in items:
            if key=='phase':continue
            if key in ('request_id','native_request_id','response_id'):
                pattern=_RESPONSE if key=='response_id' else _ID
                match=pattern.fullmatch(value)
                if match is None or any(int(part)>UINT for part in match.groups()):return None
                result[key]=value
            elif key in ('resident_epoch','weight_allocation_id'):
                if _HEX.fullmatch(value) is None:return None
                result[key]=value
            elif key in _ENUM:
                if value not in _ENUM[key]:return None
                result[key]=_ENUM[key][value]
            elif key=='cancelled':
                if value not in ('true','false'):return None
                result[key]=value=='true'
            else:
                if re.fullmatch('0|[1-9][0-9]{0,19}',value) is None or int(value)>UINT:return None
                result[key]=int(value)
        if layout=='initial' and (result['native_request_id']!=result['request_id'] or result['step_mode']!=0):return None
        if layout=='mtp2_step' and result['step_mode'] not in (1,2):return None
        if layout=='mtp2_context_tail' and (result['reason']!=2 or result['reserve_tokens']!=12
                or result['resident_context_tokens']!=262144 or result['mtp_drafts']!=2
                or not 0<result['context_tail_tokens']<12
                or not 0<=result['start_position']<=result['end_position']<=262144
                or not 0<result['generated_tokens']<=65536):return None
        return _Numeric(result)
    except (UnicodeError,ValueError,KeyError):return None

def provenance(record):
    """Only allowlisted owned-process metadata; caller text is never copied."""
    try:
        child=record['child'];startup=record['startup_id']
        if type(startup) is not str or re.fullmatch('[0-9a-f]{32}',startup) is None:raise ValueError
        if type(child['pid']) is not int or not 1<child['pid']<=UINT or type(child['uid']) is not int or not 0<=child['uid']<=UINT:raise ValueError
        started=child['started']
        if type(started) is not str or len(started.encode())>256:raise ValueError
        hashes={name:record[name] for name in ('binary_sha256','engine_source_inventory_sha256','argv_sha256')}
        if any(type(v) is not str or _HEX.fullmatch(v) is None for v in hashes.values()):raise ValueError
        return {'schema_version':1,'phase':'journal_header','startup_id':startup,'child_pid':child['pid'],
                'child_uid':child['uid'],'child_start_sha256':hashlib.sha256(started.encode()).hexdigest(),
                **hashes,'live_release_authorized':False,'runtime_acceptance':False}
    except (KeyError,TypeError,ValueError):raise DistributionError('numeric journal provenance unavailable') from None

def _valid_header(row):
    keys={'schema_version','phase','startup_id','child_pid','child_uid','child_start_sha256',
          'binary_sha256','engine_source_inventory_sha256','argv_sha256','live_release_authorized','runtime_acceptance'}
    if (type(row) is not dict or set(row)!=keys or type(row['schema_version']) is not int
        or row['schema_version']!=1 or row['phase']!='journal_header' or type(row['child_uid']) is not int or row['child_uid']!=os.getuid()
        or type(row['child_pid']) is not int or not 1<row['child_pid']<=UINT
        or type(row['startup_id']) is not str or re.fullmatch('[0-9a-f]{32}',row['startup_id']) is None
        or any(type(row[k]) is not str or _HEX.fullmatch(row[k]) is None for k in ('child_start_sha256','binary_sha256','engine_source_inventory_sha256','argv_sha256'))
        or row['live_release_authorized'] is not False or row['runtime_acceptance'] is not False):raise ValueError

def _owned_header(path):
    path=no_symlinks(path);info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>MAX_BYTES:raise ValueError
    with path.open('rb') as source:raw=source.readline(2049)
    if len(raw)>2048:raise ValueError
    _valid_header(json.loads(raw))

def _record_safe(record):
    if type(record) is not _Numeric or record.issuer is not _ISSUER:return False
    row=dict(record.fields);phase=row.get('phase');layout='initial' if phase=='mtp2_step' and 'native_request_id' in row else phase
    if layout not in _LAYOUT or set(row)!=set(_LAYOUT[layout])|{'phase'}:return False
    for key,value in row.items():
        if key=='phase':continue
        if key in ('request_id','native_request_id','response_id'):
            pattern=_RESPONSE if key=='response_id' else _ID
            if type(value) is not str or pattern.fullmatch(value) is None:return False
        elif key in ('resident_epoch','weight_allocation_id'):
            if type(value) is not str or _HEX.fullmatch(value) is None:return False
        elif key=='cancelled':
            if type(value) is not bool:return False
        elif key in _ENUM:
            if type(value) is not int or value not in _ENUM[key].values():return False
        elif type(value) is not int or not 0<=value<=UINT:return False
    return True

class NumericJournal:
    """Reader never waits for writer I/O; losses remain explicit in snapshot()."""
    def __init__(self,run_root,header,*,max_bytes=MAX_BYTES,queue_limit=QUEUE_LIMIT,sink_factory=None):
        if type(max_bytes) is not int or not 2048<=max_bytes<=MAX_BYTES or type(queue_limit) is not int or not 1<=queue_limit<=QUEUE_LIMIT:raise DistributionError('numeric journal bound differs')
        try:_valid_header(header)
        except (ValueError,TypeError):raise DistributionError('numeric journal header differs') from None
        self.directory=no_symlinks(Path(run_root)/'mtp2-native-numerics');self.header=dict(header)
        self.max_bytes=max_bytes;self.queue=queue.Queue(queue_limit);self.lock=threading.Lock();self.done=threading.Event()
        self.stats={k:0 for k in ('input_bytes','lines','parsed','written','ignored_lines','critical_rejects','overlong_lines',
            'partial_eof_lines','queue_drops','cap_drops','writer_errors','read_errors','writer_join_timeouts','rejected_records','after_close_drops','rotations','retired_previous','journal_bytes','last_seq_written')}
        self.producer_closed=False;self.writer_closed=False;self.buffer=bytearray();self.discard=False
        self.factory=sink_factory;self.writer=threading.Thread(target=self._write,daemon=True);self.writer.start()

    def _bump(self,key,count=1):
        with self.lock:self.stats[key]=min(UINT,self.stats[key]+count)

    def snapshot(self):
        with self.lock:result=dict(self.stats)
        result.update(schema_version=1,startup_id=self.header['startup_id'],producer_closed=self.producer_closed,
            writer_closed=self.writer_closed,queue_pending=self.queue.qsize(),live_release_authorized=False,runtime_acceptance=False)
        return result

    def offer(self,record):
        if not _record_safe(record):
            self._bump('rejected_records');return False
        if self.done.is_set():self._bump('after_close_drops');return False
        with self.lock:
            self.stats['parsed']=min(UINT,self.stats['parsed']+1);seq=self.stats['parsed']
        try:self.queue.put_nowait((seq,record));return True
        except queue.Full:self._bump('queue_drops');return False

    def feed(self,chunk):
        if type(chunk) is not bytes or len(chunk)>CHUNK:self._bump('read_errors');return
        self._bump('input_bytes',len(chunk));start=0
        while start<len(chunk):
            end=chunk.find(b'\n',start);complete=end>=0
            if not complete:end=len(chunk)
            piece=chunk[start:end]
            if not self.discard:
                if len(self.buffer)+len(piece)>MAX_LINE:
                    self.buffer.clear();self.discard=True;self._bump('overlong_lines')
                else:self.buffer.extend(piece)
            if complete:
                self._bump('lines')
                if not self.discard:
                    record=parse_line(bytes(self.buffer))
                    if record is not None:self.offer(record)
                    elif self.buffer.startswith((b'LILY_MTP2_RESIDENT',b'LILY_ENGINE_EFFECTIVE',b'LILY_QSA_ROUTE_EFFECTIVE')) or (self.buffer.startswith(b'[lily-meta]') and re.search(rb' phase=(mtp2_policy|mtp2_step|mtp2_released|mtp2_reservation|mtp2_continuation_budget|mtp2_context_tail)(?: |$)',self.buffer)):
                        self._bump('critical_rejects')
                    else:self._bump('ignored_lines')
                self.buffer.clear();self.discard=False
            start=end+1

    def drain(self,stream):
        try:
            read=getattr(stream,'read1',stream.read)
            while chunk:=read(CHUNK):self.feed(chunk)
        except Exception:self._bump('read_errors')
        finally:
            if self.buffer or self.discard:self._bump('partial_eof_lines')
            self.buffer.clear();self.discard=False;self.producer_closed=True;self.done.set()

    def _open(self):
        directory=private_directory(self.directory);current=directory/'current.jsonl';previous=directory/'previous.jsonl'
        for path in (current,previous):
            if path.exists() or path.is_symlink():_owned_header(path)
        if previous.exists():previous.unlink();self._bump('retired_previous')
        if current.exists():os.rename(current,previous);self._bump('rotations')
        fd=os.open(current,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        return os.fdopen(fd,'wb')

    def _write(self):
        sink=None
        try:
            sink=self.factory() if self.factory is not None else self._open()
            header=json.dumps(self.header,separators=(',',':')).encode()+b'\n'
            sink.write(header);sink.flush();self._bump('journal_bytes',len(header))
            while not self.done.is_set() or not self.queue.empty():
                try:seq,record=self.queue.get(timeout=.1)
                except queue.Empty:continue
                row=json.dumps({'seq':seq,**dict(record.fields)},separators=(',',':')).encode()+b'\n'
                with self.lock:full=self.stats['journal_bytes']+len(row)>self.max_bytes-1024
                if full:self._bump('cap_drops');continue
                sink.write(row);sink.flush()
                with self.lock:
                    self.stats['journal_bytes']+=len(row);self.stats['written']+=1;self.stats['last_seq_written']=seq
            footer=json.dumps({'phase':'journal_footer',**self.snapshot()},separators=(',',':')).encode()+b'\n'
            if len(footer)>1024:raise ValueError
            sink.write(footer);sink.flush();self._bump('journal_bytes',len(footer))
        except Exception:self._bump('writer_errors')
        finally:
            if sink is not None:
                try:sink.close()
                except Exception:self._bump('writer_errors')
            self.writer_closed=True

    def close(self,timeout=1):
        self.done.set();self.writer.join(timeout=timeout)
        if self.writer.is_alive():self._bump('writer_join_timeouts')
        return self.snapshot()
