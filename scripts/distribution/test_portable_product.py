"""Product-only factory tests: no local deployment, review records, listener or model."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/compat/src'))
from compat_api import app,portable,profile

class ProductFactoryTests(unittest.TestCase):
 def test_implicit_factory_refused_before_any_private_file_read(self):
  with patch.object(Path,'read_text',side_effect=AssertionError('must not read implicit deployment')):
   with self.assertRaises(RuntimeError):app.create_app()
  self.assertFalse(hasattr(app.Settings,'from_project'))
 def test_legacy_profile_shim_always_fails_closed_without_private_paths(self):
  for env in [{},{'COMPAT_STAGING_PROFILE_PATH':'missing','COMPAT_STAGING_PROFILE_SHA256':'0'*64}]:
   with self.assertRaises(profile.CompatProfileError):profile.resolve_compat_identity('qwen3.8-flash-next-lily-q4-64k',environ=env)
 def test_portable_factory_passes_verified_explicit_settings(self):
  source=Path(__file__).resolve().parents[2]
  sys.path.insert(0,str(source/'scripts/distribution'))
  from profile_registry import load_registry
  trusted={'source_root':source,'registry':load_registry(source),'checkpoint':{'files':[]},'profile':{'public_alias':'qwen3.8-flash-next-lily-q4-64k','runtime_model_id':'Qwen3.8-Flash-Next','context_tokens':65536},'config':{'ports':{'litellm':14000}}}
  with patch.object(portable,'_validated',return_value=trusted) as validate,patch.object(app,'create_app',return_value='factory-marker') as create:
   self.assertEqual(portable.create_compat_app(),'factory-marker')
  validate.assert_called_once();settings=create.call_args.kwargs['settings'];self.assertEqual(settings.context_limit_tokens,65536);self.assertEqual(settings.runtime_model_id,'Qwen3.8-Flash-Next');self.assertEqual(settings.litellm_base_url,'http://127.0.0.1:14000')

if __name__=='__main__':unittest.main()
