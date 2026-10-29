"""Installer contract tests; synthetic archives are not host-runtime evidence."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bugate_integrations', ROOT / 'src/bugate/integrations.py')
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


class InstallBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bugate-install-boundary-')
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_hc14_owned_block_remove_preserves_user_bytes(self):
        user = b'# User preferences\n- id: user-tool\n  disabled: true\n'
        block = integration._block(self.root, self.root / 'venv/bin/python')
        combined = integration._replace_block(user, self.root, block, None)
        self.assertEqual(integration._replace_block(combined, self.root, b'', hashlib.sha256(block).hexdigest()), user)

    def test_hc01_same_block_replacement_is_byte_idempotent(self):
        block = integration._block(self.root, self.root / 'python')
        original = integration._replace_block(b'# default\n[]\n', self.root, block, None)
        self.assertEqual(integration._replace_block(original, self.root, block, integration._hash(block)), original)

    def test_hc14_edited_block_is_not_overwritten(self):
        block = integration._block(self.root, self.root / 'python')
        changed = block.replace(b'python', b'other')
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._replace_block(changed, self.root, b'', integration._hash(block))
        self.assertEqual(caught.exception.code, 'INTEGRATION_CONFLICT')

    def test_ac12_profile_cannot_rebind_other_project(self):
        block = integration._block(self.root / 'first', self.root / 'python')
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._replace_block(block, self.root / 'second', block, None)
        self.assertEqual(caught.exception.code, 'INTEGRATION_CONFLICT')

    def test_ac13_symlink_scope_escape(self):
        (self.root / 'link').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._safe(self.root, self.root / 'link/file')
        self.assertEqual(caught.exception.code, 'PATH_ESCAPE')

    def test_ac13_profile_path_escape(self):
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._paths(self.root, '../other', self.root / 'dsh')
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')

    def test_ac13_archive_link_and_parent_escape(self):
        for name, link in [('package/../../escape', False), ('package/link', True)]:
            with self.subTest(name=name):
                package = self.root / 'bad.tgz'
                with tarfile.open(package, 'w:gz') as archive:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/tmp'
                    archive.addfile(member, io.BytesIO(b''))
                with self.assertRaises(integration.IntegrationError) as caught:
                    integration._unpack(package, self.root / 'extract')
                self.assertEqual(caught.exception.code, 'PACKAGE_INVALID')

    def test_ac15_missing_python(self):
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._run(['/does/not/exist'], cwd=self.root, env=os.environ.copy())
        self.assertEqual(caught.exception.code, 'DEPENDENCY_MISSING')

    def test_ac15_output_bound(self):
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._run([sys.executable, '-c', "print('a'*10000)"], cwd=self.root, env=os.environ.copy(), limit=512)
        self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')

    def test_ac16_timeout_reaps_process(self):
        pidfile = self.root / 'pid'
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._run([sys.executable, '-c',
                f"import os,time;open({str(pidfile)!r},'w').write(str(os.getpid()));time.sleep(20)"],
                cwd=self.root, env=os.environ.copy(), timeout=.2)
        self.assertEqual(caught.exception.code, 'PYTHON_TIMEOUT')
        self.assertEqual(caught.exception.exit_code, 124)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_ac17_atomic_write_does_not_follow_symlink(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'unchanged')
        target = self.root / 'target'
        target.symlink_to(outside)
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._atomic(target, b'overwrite')
        self.assertEqual(caught.exception.code, 'PATH_ESCAPE')
        self.assertEqual(outside.read_bytes(), b'unchanged')

    def test_wheel_abi_mismatch_is_explicit_before_install(self):
        inventory = {'files': {'native-cp000-fake-platform.whl': '0'*64}, 'platform': 'other', 'machine': 'other', 'platform_tag': 'other', 'soabi': 'other'}
        with self.assertRaises(integration.IntegrationError) as caught:
            integration._check_wheel_target(sys.executable, inventory, self.root, os.environ.copy())
        self.assertEqual(caught.exception.code, 'PYTHON_ABI_UNSUPPORTED')
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn('actual', caught.exception.details)

    def test_missing_unselected_host_is_nonfatal(self):
        value = integration.doctor(self.root, dsh='/does/not/exist')
        self.assertEqual(value['host'], 'missing')
        self.assertEqual(value['status'], 'not_selected')

    def test_ac11_second_process_lock_conflicts(self):
        code = f"import importlib.util; s=importlib.util.spec_from_file_location('i',{str(ROOT / 'src/bugate/integrations.py')!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m);from pathlib import Path;\nwith m._lock(Path({str(self.root)!r})): print('unexpected')"
        with integration._lock(self.root):
            with self.assertRaises(integration.IntegrationError) as caught:
                integration._run([sys.executable, '-c', code], cwd=self.root, env=os.environ.copy())
        self.assertEqual(caught.exception.code, 'HOST_COMMAND_FAILED')
        self.assertIn('Another integration operation owns the lock', caught.exception.details['stderr'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
