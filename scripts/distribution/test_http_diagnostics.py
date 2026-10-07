"""Same-helper/header mock response segmentation; these checks are harness-only."""
import errno,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError,URLError
sys.path.insert(0,str(Path(__file__).resolve().parent))
import agent
from common import DistributionError
class Reply:
 def __init__(self,status=200,body=b'{"status":"healthy"}',kind='application/json',error=None):self.status=status;self.body=body;self.headers={'Content-Type':kind};self.error=error
 def __enter__(self):return self
 def __exit__(self,*args):return False
 def read(self,size):
  if self.error:raise self.error
  return self.body[:size]
class HttpDiagnosticsTests(unittest.TestCase):
 def run_reply(self,reply,**kwargs):
  events=[]
  with patch.object(agent,'build_opener') as factory:
   factory.return_value.open.return_value=reply
   result=agent.http_json('http://127.0.0.1:14000/health/readiness',diagnostics=events,**kwargs)
   request=factory.return_value.open.call_args.args[0]
  return result,events,request
 def test_json_success_and_same_headers(self):
  result,events,request=self.run_reply(Reply(),token='synthetic-own-key')
  self.assertEqual(result,(200,{'status':'healthy'}));self.assertEqual(events[-1]['stage'],'complete');self.assertTrue(events[-1]['json_decode_success'])
  self.assertEqual(request.get_header('Content-type'),'application/json');self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-own-key')
  self.assertNotIn('synthetic-own-key',json.dumps(events))
 def test_health_nonjson_keeps_transport200_but_never_guesses_ready(self):
  result,events,_=self.run_reply(Reply(body=b'OK',kind='text/plain'))
  self.assertEqual(result,(0,{}));self.assertEqual(events[-1]['http_status'],200);self.assertEqual(events[-1]['body_length'],2);self.assertEqual(events[-1]['exception_class'],'JSONDecodeError')
  self.assertFalse(events[-1]['json_decode_success'])
 def test_empty_body_is_not_admin_json_success(self):
  result,events,_=self.run_reply(Reply(body=b''));self.assertEqual(result,(0,{}));self.assertEqual(events[-1]['http_status'],200);self.assertEqual(events[-1]['body_length'],0)
 def test_admin_invalid_body_and_secret_message_never_recorded(self):
  result,events,_=self.run_reply(Reply(body=b'not-json synthetic-secret'))
  self.assertEqual(result,(0,{}));self.assertNotIn('synthetic-secret',json.dumps(events));self.assertNotIn('body',events[-1]);self.assertNotIn('url',events[-1])
 def test_network_error_errno_is_separate_from_http(self):
  events=[]
  with patch.object(agent,'build_opener') as factory:
   factory.return_value.open.side_effect=URLError(OSError(errno.ECONNREFUSED,'synthetic-password'))
   self.assertEqual(agent.http_json('http://127.0.0.1:14000/health/readiness',diagnostics=events),(0,{}))
  self.assertIsNone(events[-1]['http_status']);self.assertEqual(events[-1]['errno'],errno.ECONNREFUSED);self.assertNotIn('synthetic-password',json.dumps(events))
 def test_http_auth_status_not_conflated_with_network(self):
  events=[]
  with patch.object(agent,'build_opener') as factory:
   factory.return_value.open.side_effect=HTTPError('http://127.0.0.1:14000/private',401,'synthetic-secret',{'Content-Type':'application/json'},None)
   self.assertEqual(agent.http_json('http://127.0.0.1:14000/private',diagnostics=events),(401,{}))
  self.assertEqual(events[-1]['http_status'],401);self.assertEqual(events[-1]['stage'],'http_error');self.assertNotIn('synthetic-secret',json.dumps(events))
 def test_redirect_handler_does_not_forward_credentials(self):
  self.assertIsNone(agent._NoControlRedirect().redirect_request(None,None,302,'unused',{},'https://example.invalid/'))
 def test_body_read_failure_retains_observed_status(self):
  result,events,_=self.run_reply(Reply(error=TimeoutError('synthetic-secret')))
  self.assertEqual(result,(0,{}));self.assertEqual(events[-1]['http_status'],200);self.assertEqual(events[-1]['exception_class'],'TimeoutError');self.assertNotIn('synthetic-secret',json.dumps(events))
 def test_only_exact_pinned_manager_health_accepts_plain_OK(self):
  events=[]
  with patch.object(agent,'build_opener') as factory:
   factory.return_value.open.return_value=Reply(body=b'OK',kind='text/plain; charset=utf-8')
   result=agent.http_json('http://127.0.0.1:18081/health',health_contract='llama_swap_health',diagnostics=events)
  self.assertEqual(result[0],200);self.assertEqual(events[-1]['stage'],'health_contract_match');self.assertFalse(events[-1]['json_decode_success'])
 def test_plain_health_wrong_body_or_path_never_ready(self):
  with patch.object(agent,'build_opener') as factory:
   factory.return_value.open.return_value=Reply(body=b'<html>OK</html>',kind='text/html')
   self.assertEqual(agent.http_json('http://127.0.0.1:18081/health',health_contract='llama_swap_health'),(0,{}))
  with self.assertRaises(DistributionError):agent.http_json('http://127.0.0.1:18081/key/generate',health_contract='llama_swap_health')
 def test_manager_health_500_newline_and_other_plain_rejected(self):
  for status,body in [(500,b'OK'),(200,b'OK\n'),(200,b'healthy')]:
   with patch.object(agent,'build_opener') as factory:
    factory.return_value.open.return_value=Reply(status=status,body=body,kind='text/plain')
    self.assertEqual(agent.http_json('http://127.0.0.1:18081/health',health_contract='llama_swap_health'),(0,{}))
 def test_url_credentials_refused(self):
  with self.assertRaises(DistributionError):agent.http_json('http://user:synthetic-secret@127.0.0.1:14000/health/readiness')
 def test_wait_http_uses_same_helper_diagnostics_and_does_not_accept_bad_decode(self):
  events=[]
  with patch.object(agent,'build_opener') as factory,patch.object(agent.time,'monotonic',side_effect=[0,0,2]),patch.object(agent.time,'sleep'):
   factory.return_value.open.return_value=Reply(body=b'OK',kind='text/plain')
   with self.assertRaises(DistributionError):agent.wait_http('http://127.0.0.1:14000/health/readiness',seconds=1,diagnostics=events)
  self.assertEqual(events[-1]['http_status'],200);self.assertFalse(events[-1]['json_decode_success'])
if __name__=='__main__':unittest.main(verbosity=2)
