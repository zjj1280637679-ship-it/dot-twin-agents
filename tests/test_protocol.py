import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from dot_twin import prepare
from dot_twin.contracts import MODES

ROOT = Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.TestCase):
    def test_four_modes_preserve_exact_delta_and_native_shape(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                delta = '  新目标：保留\n细节与分歧 🧭\n'
                value = dict(task_name='verify_delivery_2', delta=delta, mode=mode)
                original = copy.deepcopy(value)
                actual = prepare(value)
                self.assertEqual(set(actual), {'task_name','message','fork_turns'})
                self.assertEqual(actual['task_name'], value['task_name'])
                self.assertEqual(actual['fork_turns'], 'all')
                self.assertTrue(actual['message'].endswith('Task difference:\n'+delta))
                self.assertEqual(value, original)

    def test_default_open_matches_explicit(self):
        base = dict(task_name='check_delivery', delta='Find missing closure.')
        self.assertEqual(prepare(base), prepare(dict(base, mode='open')))

    def test_reject_unknown_or_missing_fields(self):
        for value in ({}, {'task_name':'test'}, {'delta':'test'},
                      {'task_name':'test','delta':'test','fork_turns':'none'},
                      {'task_name':'test','delta':'test','api_key':'secret'},
                      {'task_name':'test','delta':'test','history':[]}, [], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare(value)

    def test_reject_bad_names(self):
        for name in ('', 'A', 'a-b', '../foo', '0foo', 'x'*65, 'a\n', 1, True, None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare(dict(task_name=name, delta='x'))

    def test_name_boundary(self):
        self.assertEqual(prepare(dict(task_name='x'*64, delta='x'))['task_name'], 'x'*64)

    def test_reject_bad_deltas(self):
        for delta in ('', ' \n', '\ud800', 'a\x00b', {}, None, True):
            with self.subTest(delta=repr(delta)), self.assertRaises(ValueError):
                prepare(dict(task_name='x', delta=delta))

    def test_reject_unknown_modes(self):
        for mode in ('auto', 'OPEN', None, True, {}, 3):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                prepare(dict(task_name='x', delta='x', mode=mode))

    def test_task_text_is_data_not_executed(self):
        delta = '__import__("os").system("exit 9")'
        self.assertTrue(prepare(dict(task_name='x',delta=delta))['message'].endswith(delta))

    def call(self, text):
        return subprocess.run([sys.executable,'-B','-m','dot_twin','prepare'],input=text,
                              text=True,capture_output=True,cwd=ROOT)

    def test_cli_success_matches_library(self):
        value = dict(task_name='check', delta='审查遗漏', mode='verify')
        result = self.call(json.dumps(value))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), prepare(value))
        self.assertEqual(result.stderr, '')

    def test_cli_rejects_duplicate_and_nonfinite_json(self):
        for text in ('{"task_name":"a","task_name":"b","delta":"private"}',
                     '{"task_name":"a","delta":NaN}',
                     '{"task_name":"a","delta":Infinity}',
                     '{"task_name":"a","delta":"private","mode":"bad"}',
                     'broken private text', '[]', '',
                     '{"task_name":"a","delta":"\\ud800"}'):
            with self.subTest(text=text):
                result = self.call(text)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout), {'error':'invalid_request'})
                self.assertNotIn('private', result.stdout+result.stderr)
                self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
