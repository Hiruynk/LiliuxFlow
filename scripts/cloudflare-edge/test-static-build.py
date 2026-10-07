# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('edge_static_build', Path(__file__).with_name('static-build.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StaticBuildTests(unittest.TestCase):
    def test_exact_source_bytes_and_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / 'public'
            result = MODULE.build(MODULE.ROOT / 'assets/welcome', output)
            self.assertEqual(result['source_asset_count'], 9)
            for asset in MODULE.ASSETS:
                source = (MODULE.ROOT / 'assets/welcome' / asset).read_bytes()
                generated = (output / 'liliuxflow-welcome' / asset).read_bytes()
                self.assertEqual(generated.replace(MODULE.EDGE_GUARD_ATTRIBUTE, b'') if asset == 'index.html' else generated, source)
            source_html = (MODULE.ROOT / 'assets/welcome/index.html').read_bytes()
            generated_html = (output / 'index.html').read_bytes()
            self.assertEqual(generated_html.count(MODULE.EDGE_GUARD_ATTRIBUTE), 1)
            self.assertEqual(generated_html.replace(MODULE.EDGE_GUARD_ATTRIBUTE, b''), source_html)
            self.assertTrue(result['html_and_style_transformed'])
            self.assertFalse(result['css_transformed'])
            self.assertFalse(result['visual_design_transformed'])
            self.assertNotIn(b'data-edge-backend-guard', source_html)
            headers = (output / '_headers').read_text()
            self.assertIn("connect-src 'self'", headers)
            self.assertNotIn('/ui', headers)
            self.assertNotIn('/v1', headers)
            self.assertNotIn('/api', headers)
            MODULE.build(MODULE.ROOT / 'assets/welcome', output, replace=True)
            (output / 'index.html').write_text('changed by someone else')
            with self.assertRaisesRegex(ValueError, 'changed'):
                MODULE.build(MODULE.ROOT / 'assets/welcome', output, replace=True)

    def test_unknown_output_or_links_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / 'public'
            output.mkdir()
            with self.assertRaisesRegex(ValueError, 'verified'):
                MODULE.build(MODULE.ROOT / 'assets/welcome', output, replace=True)
            linked = Path(directory).resolve() / 'linked'
            linked.symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'canonical'):
                MODULE.build(MODULE.ROOT / 'assets/welcome', linked, replace=True)

    def test_guard_marker_requires_exact_unmarked_body(self):
        for html in (b'<BODY></BODY>', b'<body></body>', b'<body a="x"><body b="y">', b'<body data-edge-backend-guard="false">'):
            with self.assertRaisesRegex(ValueError, 'unmarked'):
                MODULE.edge_html(html)


if __name__ == '__main__':
    unittest.main()
