import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plugin_builder', ROOT/'scripts/build_plugin.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root/'plugin'
        shutil.copytree(ROOT/'plugin', self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_exact_bytes_and_reproducible(self):
        a,b = self.root/'a.zip',self.root/'b.zip'
        report = builder.build(self.source, a)
        builder.build(self.source, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(report['sha256'], hashlib.sha256(a.read_bytes()).hexdigest())
        with zipfile.ZipFile(a) as archive:
            self.assertEqual(archive.namelist(), list(builder.MEMBERS))
            for name in builder.MEMBERS:
                self.assertEqual(archive.read(name), (self.source/name).read_bytes())
        self.assertEqual((ROOT/'LICENSE').read_bytes(), (self.source/'LICENSE').read_bytes())

    def test_reject_extra_file(self):
        (self.source/'secret.txt').write_text('synthetic')
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_reject_missing_file(self):
        (self.source/'LICENSE').unlink()
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_reject_symlink_file(self):
        (self.source/'LICENSE').unlink()
        (self.source/'LICENSE').symlink_to(ROOT/'LICENSE')
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_reject_symlink_source(self):
        link = self.root/'link'
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError): builder.validate(link)

    def test_reject_output_inside_source(self):
        with self.assertRaises(ValueError): builder.build(self.source, self.source/'out.zip')

    def test_reject_native_tool_manifest(self):
        path = self.source/'plugin.json'
        value=json.loads(path.read_text());value['mcpServers']={}
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_reject_oversized_file(self):
        (self.source/'LICENSE').write_text('x'*32769)
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_privacy_tripwire(self):
        path = self.source/'skills/dot-twin/SKILL.md'
        path.write_text(path.read_text()+'\n/workspace/private/example\n')
        with self.assertRaises(ValueError): builder.validate(self.source)

    def test_license_present(self):
        (self.source/'LICENSE').write_text('missing')
        with self.assertRaises(ValueError): builder.validate(self.source)


if __name__ == '__main__':
    unittest.main()
