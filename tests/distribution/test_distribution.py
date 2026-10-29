"""NL-01/02: real inventory checks and adversarial release contamination controls."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
from distribution import check_source, check_wheel, check_python, safe_name, resource_files, CORE_MODULES

class DistributionTests(unittest.TestCase):
    def test_current_source_has_only_v2_entries_and_explicit_resources(self):
        self.assertEqual(check_source(ROOT)['status'], 'PASS')

    def test_retired_import_and_process_path_are_rejected(self):
        for content in (b'from .legacy import check_precode', b'import memory_bus',
                        b'argv = ["python", "scripts/check_bugate_v13_semantics.py"]'):
            with self.assertRaisesRegex(ValueError, 'retired runtime'):
                check_python('candidate.py', content)

    def test_credentials_paths_and_fixtures_are_rejected(self):
        for name in ('package/.env', 'package/.env.production', 'package/id.key',
                     'package/sut/source.json', 'package/fixtures/business.json',
                     'package/.codex/hooks.json', '../escape'):
            with self.assertRaises(ValueError):
                safe_name(name)

    def test_wheel_checks_actual_members_not_filename(self):
        resources = resource_files(ROOT)
        def wheel(extra=None, entries="[console_scripts]\nbugate = bugate.cli:main\n"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as z:
                for name in CORE_MODULES: z.writestr('bugate/' + name, '')
                for name in resources: z.writestr('bugate/resources/' + name, '{}')
                for name in ('METADATA', 'WHEEL', 'licenses/LICENSE', 'RECORD'):
                    z.writestr('bugate_core-2.0.0a2.dist-info/' + name, '')
                z.writestr('bugate_core-2.0.0a2.dist-info/entry_points.txt', entries)
                if extra: z.writestr(extra, '')
            return output.getvalue()
        self.assertEqual(check_wheel(wheel(), resources)['status'], 'PASS')
        for name in ('bugate/legacy.py', 'bugate/resources/scripts/check_bugate.py',
                     'bugate/resources/.claude/settings.json', 'bugate/unowned.json'):
            with self.assertRaises(ValueError): check_wheel(wheel(name), resources)
        for entries in ('[console_scripts]\nbugate-role = bugate.cli:main\n',
                        '[console_scripts]\nbugate = bugate.cli:main\nbugate-update = bugate.cli:main\n',
                        '[console_scripts]\nbugate = other:main\n',
                        '[other_group]\nbugate = bugate.cli:main\n'):
            with self.assertRaisesRegex(ValueError, 'only bugate'):
                check_wheel(wheel(entries=entries), resources)
        with self.assertRaisesRegex(ValueError, 'metadata inventory'):
            check_wheel(wheel('old.dist-info/entry_points.txt'), resources)

if __name__ == '__main__': unittest.main()
