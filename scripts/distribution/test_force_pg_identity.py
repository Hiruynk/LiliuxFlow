# SPDX-License-Identifier: Apache-2.0
"""Exercise the real PG identity guard using inert private PID-file fixtures."""
import datetime,hashlib,os,pathlib,tempfile,types,unittest
from unittest.mock import Mock,patch
import agent,forced_stop
from common import DistributionError

class ForcePGIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='force-PG-metadata-');self.addCleanup(self.temporary.cleanup)
        self.data=pathlib.Path(self.temporary.name).resolve();self.pgdata=self.data/'postgres/data';self.pgdata.mkdir(parents=True,mode=0o700)
        self.tools=self.data/'inert-tools';self.tools.mkdir(mode=0o700)
        started=datetime.datetime.now().replace(microsecond=0)
        self.epoch=int(started.timestamp());self.started=started.strftime('%a %b %d %H:%M:%S %Y')
        self.record={'pid':12002,'ppid':12000,'pgid':12000,'uid':os.getuid(),'started':self.started,'command_sha256':'a'*64}
        self.registry={'postgres':self.record};self.trusted={'data_root':self.data,'config':{'ports':{'postgresql':15432}}}
        self.pidfile=self.pgdata/'postmaster.pid';self.prefix=str(self.tools/'postgres')+' -D '+str(self.pgdata)
        self.fs=types.SimpleNamespace(identity=forced_stop.identity,exact=Mock(return_value=True),command=Mock(return_value=self.prefix))
        self.write()
    def write(self,*,pid=12002,data=None,epoch=None,port=15432):
        self.pidfile.write_text(f'{pid}\n{data if data is not None else self.pgdata}\n{self.epoch if epoch is None else epoch}\n{port}\n');self.pidfile.chmod(0o600)
    def call(self):
        with patch.object(agent,'postgres_tools',return_value=self.tools),patch.object(agent,'private_run',side_effect=AssertionError('metadata guard must never stop a DB')),patch.object(forced_stop.os,'kill',side_effect=AssertionError('PG metadata must never signal')):
            return agent._force_pg_identity(self.trusted,self.registry,self.fs)
    def test_exact_pidfile_PGDATA_port_birth_command_accepted_without_execution(self):
        self.assertEqual(self.call(),(self.record,self.pidfile.read_bytes()));self.fs.exact.assert_called_once_with(self.record)
    def test_wrong_PID_PGDATA_or_port_refused_by_actual_guard(self):
        for change in ({'pid':99999},{'data':self.data/'foreign-data'},{'port':15433}):
            with self.subTest(change=change):self.write(**change);self.assertRaises(DistributionError,self.call)
    def test_wrong_birth_or_binary_command_refused_by_actual_guard(self):
        self.write(epoch=self.epoch-6);self.assertRaises(DistributionError,self.call);self.write()
        for command in (str(self.tools/'foreign-postgres')+' -D '+str(self.pgdata),self.prefix+'-foreign','postgres -D '+str(self.pgdata)):
            with self.subTest(command=command):self.fs.command.return_value=command;self.assertRaises(DistributionError,self.call)
    def test_absent_foreign_owner_and_unregistered_PIDfile_never_adopted(self):
        self.fs.exact.return_value=False;self.assertRaises(DistributionError,self.call);self.fs.exact.return_value=True
        self.registry['postgres']={**self.record,'uid':0};self.assertRaises(DistributionError,self.call)
        self.registry['postgres']=None;self.assertRaises(DistributionError,self.call)
        self.pidfile.unlink();self.assertEqual(self.call(),(None,None))
    def test_malformed_private_mode_and_symlink_PIDfile_refused(self):
        self.pidfile.write_text('bad\n'+str(self.pgdata)+'\nbad\n15432\n');self.assertRaises(ValueError,self.call);self.write()
        self.pidfile.chmod(0o644);self.assertRaises(DistributionError,self.call);self.pidfile.chmod(0o600)
        original=self.pidfile.with_name('original.pid');self.pidfile.rename(original);self.pidfile.symlink_to(original);self.assertRaises(DistributionError,self.call)

if __name__=='__main__':unittest.main(verbosity=2)
