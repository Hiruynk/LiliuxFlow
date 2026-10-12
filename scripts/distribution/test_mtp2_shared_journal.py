# SPDX-License-Identifier: Apache-2.0
"""Pure CPU numeric journal privacy/ownership/bounds; no captured private trace."""
import hashlib,io,json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
S=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
sys.path.insert(0,str(S))
import mtp2_shared_journal as j
from common import DistributionError
E='a'*64;R='req-1791623462-1'
RES=f'LILY_MTP2_RESIDENT protocol=1 resident_context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=2 weight_load_generation=1 weight_load_count=1 weight_allocation_id={E} weight_buffer_count=20 weight_allocation_bytes=100000 paged_weight_bytes=0'.encode()
RELEASE=f'[lily-meta] request_id={R} phase=mtp2_released clock=mach_absolute_time_ns released_ns=9 resident_epoch={E} cancelled=true'.encode()
def header(startup='1'*32):
    return j.provenance({'startup_id':startup,'child':{'pid':1234,'uid':os.getuid(),'started':'SYNTHETIC'},
        'binary_sha256':'b'*64,'engine_source_inventory_sha256':'c'*64,'argv_sha256':'d'*64,
        'prompt':'PRIVATE_SENTINEL','secret':'PRIVATE_SENTINEL'})

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.root.chmod(0o700)
    def run_bytes(self,data,**kwargs):
        journal=j.NumericJournal(self.root,header(),**kwargs);journal.drain(io.BytesIO(data));status=journal.close()
        file=self.root/'mtp2-native-numerics/current.jsonl'
        return journal,status,[json.loads(row) for row in file.read_bytes().splitlines()] if file.exists() else []
    def test_exact_native_grammar_and_safe_numbers(self):
        row=dict(j.parse_line(RES).fields);self.assertEqual(row['weight_load_count'],1);self.assertEqual(row['kv_cache'],1)
        row=dict(j.parse_line(RELEASE).fields);self.assertIs(row['cancelled'],True)
        self.assertFalse(header()['live_release_authorized']);self.assertNotIn('PRIVATE_SENTINEL',json.dumps(header()))
    def test_extra_keys_duplicate_control_char_nonASCII_and_number_attacks(self):
        bad=[RES+b' prompt=PRIVATE_SENTINEL',RES+b' protocol=1',b' '+RES,RES.replace(b'protocol=1',b'protocol=01'),
             RES.replace(b'weight_load_count=1',b'weight_load_count=-1'),RES.replace(b'weight_load_count=1',b'weight_load_count=NaN'),
             RES.replace(b'weight_load_count=1',b'weight_load_count=18446744073709551616'),RES+b'\r',RES+b'\x00',RES+b'\xff',RELEASE.replace(b'cancelled=true',b'cancelled=1')]
        for value in bad:self.assertIsNone(j.parse_line(value))
    def test_constructor_header_is_strict_before_thread_start(self):
        for change in ({'prompt':'PRIVATE_SENTINEL'},{'child_pid':True},{'child_uid':False},{'runtime_acceptance':0},{'startup_id':'PRIVATE_SENTINEL'}):
            value=header();value.update(change)
            with patch.object(j.threading,'Thread') as thread:
                with self.assertRaises(DistributionError):j.NumericJournal(self.root,value)
                thread.assert_not_called()
    def test_forged_records_never_emit_text(self):
        journal=j.NumericJournal(self.root,header())
        self.assertFalse(journal.offer({'prompt':'PRIVATE_SENTINEL'}))
        self.assertFalse(journal.offer(j._Numeric({'phase':'mtp2_policy','prompt':'PRIVATE_SENTINEL'})))
        journal.drain(io.BytesIO(b''));status=journal.close()
        self.assertEqual(status['rejected_records'],2)
        self.assertNotIn('PRIVATE_SENTINEL',(self.root/'mtp2-native-numerics/current.jsonl').read_text())
    def test_partial_and_overlong_lines_cannot_accept_truncated_suffix(self):
        data=b'x'*(j.MAX_LINE+1)+RES+b'\n'+RES+b'\n'+RELEASE
        journal,status,rows=self.run_bytes(data)
        self.assertEqual(status['overlong_lines'],1);self.assertEqual(status['partial_eof_lines'],1)
        self.assertEqual(status['parsed'],1);self.assertEqual(status['written'],1)
        self.assertFalse(status['live_release_authorized'])
    def test_cap_is_bounded_and_drops_are_explicit(self):
        journal,status,rows=self.run_bytes((RES+b'\n')*20,max_bytes=2048)
        file=self.root/'mtp2-native-numerics/current.jsonl'
        self.assertLessEqual(file.stat().st_size,2048);self.assertGreater(status['cap_drops'],0)
        self.assertEqual(status['parsed'],status['written']+status['cap_drops'])
    def test_stalled_writer_never_stalls_drain_and_queue_drops_visible(self):
        entered=threading.Event();release=threading.Event()
        class Sink:
            def write(self,raw):entered.set();release.wait(3)
            def flush(self):pass
            def close(self):pass
        journal=j.NumericJournal(self.root,header(),queue_limit=2,sink_factory=Sink)
        self.assertTrue(entered.wait(1))
        reader=threading.Thread(target=journal.drain,args=(io.BytesIO((RES+b'\n')*20),));reader.start();reader.join(1)
        self.assertFalse(reader.is_alive());self.assertEqual(journal.snapshot()['queue_drops'],18)
        self.assertGreater(journal.close(timeout=.01)['writer_join_timeouts'],0)
        release.set();journal.writer.join(1)
    def test_write_error_is_not_release_or_silent_success(self):
        def fail():raise OSError('PRIVATE_SENTINEL')
        journal=j.NumericJournal(self.root,header(),sink_factory=fail);journal.drain(io.BytesIO(RELEASE+b'\n'));status=journal.close()
        self.assertEqual(status['writer_errors'],1);self.assertFalse(status['live_release_authorized'])
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(status))
    def test_rotation_retains_only_two_bounded_owned_files(self):
        for digit in '123':
            journal=j.NumericJournal(self.root,header(digit*32));journal.drain(io.BytesIO(RES+b'\n'));status=journal.close()
        directory=self.root/'mtp2-native-numerics'
        self.assertEqual({p.name for p in directory.iterdir()},{'current.jsonl','previous.jsonl'})
        self.assertEqual(status['rotations'],1);self.assertEqual(status['retired_previous'],1)
        self.assertEqual(json.loads((directory/'previous.jsonl').read_bytes().splitlines()[0])['startup_id'],'2'*32)
    def test_foreign_file_and_symlink_are_not_deleted_or_overwritten(self):
        directory=self.root/'mtp2-native-numerics';directory.mkdir(mode=0o700)
        file=directory/'current.jsonl';file.write_text('PRIVATE_SENTINEL');file.chmod(0o600)
        journal=j.NumericJournal(self.root,header());journal.drain(io.BytesIO(RES+b'\n'));status=journal.close()
        self.assertEqual(status['writer_errors'],1);self.assertEqual(file.read_text(),'PRIVATE_SENTINEL')
        file.unlink();outside=self.root/'outside';outside.write_text('preserve');file.symlink_to(outside)
        journal=j.NumericJournal(self.root,header());journal.drain(io.BytesIO(RES+b'\n'));status=journal.close()
        self.assertEqual(status['writer_errors'],1);self.assertEqual(outside.read_text(),'preserve')
    def test_nonessential_decimal_lane_ignored_not_critical(self):
        line=f'[lily-meta] request_id={R} phase=lane_released cancelled=true lane_ms=1.50'.encode()
        journal,status,rows=self.run_bytes(line+b'\n')
        self.assertEqual(status['ignored_lines'],1);self.assertEqual(status['critical_rejects'],0)

class CapacityTests(unittest.TestCase):
    def test_default4MiB_ceiling8MiB_invalid_before_thread(self):
        self.assertEqual(j.DEFAULT_BYTES,4*1024**2);self.assertEqual(j.MAX_BYTES,8*1024**2)
        for value in (True,False,0,-1,2047,8388609,2**63,8388608.0,'8388608',None):
            with self.subTest(value=value),patch.object(j.threading,'Thread') as thread:
                with self.assertRaises(DistributionError):j.NumericJournal(Path('/CPU_ONLY_UNUSED'),header(),max_bytes=value)
                thread.assert_not_called()
    def test_shared_runner_bounded_capacity_and_owned_native_launch(self):
        import ast
        source=(S/'mtp2_shared_runner.py').read_text();tree=ast.parse(source)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='NumericJournal']
        self.assertEqual(len(calls),1);self.assertEqual([(k.arg,ast.unparse(k.value)) for k in calls[0].keywords],[('max_bytes','MAX_BYTES')])
        launch=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='authenticated_launch']
        self.assertEqual(len(launch),1);self.assertEqual([ast.unparse(a) for a in launch[0].args],["installation","port","secret.encode('utf-8')","tmp"])
        popen=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='subprocess.Popen']
        self.assertEqual(len(popen),1);self.assertEqual([ast.unparse(a) for a in popen[0].args],['argv'])
        self.assertEqual({k.arg:ast.unparse(k.value) for k in popen[0].keywords},{'env':'env','pass_fds':'fds','stderr':'subprocess.PIPE'})
        captures=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='capture']
        self.assertEqual(len(captures),2);children=[n for n in captures if [ast.unparse(a) for a in n.args]==['child.pid']]
        self.assertEqual(len(children),1);self.assertEqual({k.arg:ast.unparse(k.value) for k in children[0].keywords},{'parent':'os.getpid()','executable':'installation.binary'})
        runners=[n for n in captures if [ast.unparse(a) for a in n.args]==['os.getpid()']];self.assertEqual(len(runners),1);self.assertEqual(runners[0].keywords,[])
    def test_actual_writer_over4MiB_workload8MiB_lossfree4MiB_explicitloss(self):
        class Sink:
            def __init__(self):self.data=bytearray()
            def write(self,value):self.data.extend(value)
            def flush(self):pass
            def close(self):pass
        record=j.parse_line(f'[lily-meta] request_id={R} phase=mtp2_yield resident_epoch={E} generated_tokens=12345 position=12345 reason=complete_quantum'.encode());self.assertIsNotNone(record)
        def run(cap):
            sink=Sink();journal=j.NumericJournal(Path('/CPU_ONLY_UNUSED'),header(),max_bytes=cap,sink_factory=lambda:sink)
            for index in range(24000):
                self.assertTrue(journal.offer(record))
                if index%64==63:
                    end=time.monotonic()+3
                    while journal.snapshot()['written']+journal.snapshot()['cap_drops']<index+1:
                        self.assertLess(time.monotonic(),end);time.sleep(.001)
            journal.drain(io.BytesIO(RELEASE+b'\n'));status=journal.close(timeout=3)
            return status,[json.loads(line) for line in sink.data.splitlines()],len(sink.data)
        low,lowrows,lowsize=run(j.DEFAULT_BYTES);high,highrows,highsize=run(j.MAX_BYTES)
        self.assertGreater(low['cap_drops'],0);self.assertEqual(low['queue_drops'],0);self.assertLessEqual(lowsize,j.DEFAULT_BYTES)
        self.assertEqual(high['cap_drops'],0);self.assertEqual(high['queue_drops'],0);self.assertEqual(high['parsed'],24001);self.assertEqual(high['written'],24001)
        self.assertGreater(highsize,j.DEFAULT_BYTES);self.assertLessEqual(highsize,j.MAX_BYTES);self.assertEqual(highrows[-2]['phase'],'mtp2_released');self.assertEqual([r['seq'] for r in highrows[1:-1]],list(range(1,24002)))
        for k in ('critical_rejects','overlong_lines','partial_eof_lines','writer_errors','read_errors','writer_join_timeouts','rejected_records','after_close_drops'):self.assertEqual(high[k],0)
        self.assertFalse(high['runtime_acceptance']);self.assertFalse(high['live_release_authorized'])

if __name__=='__main__':unittest.main(verbosity=2)
