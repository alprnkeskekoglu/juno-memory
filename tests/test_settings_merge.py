import json
import unittest

from juno.core import settings_merge as sm
from tests.support import realistic_settings, settings_variants


class Block(unittest.TestCase):
    def test_home_relative_paths(self) -> None:
        block = sm.build_block("/h/juno-vault", "/h")
        self.assertEqual(block["additionalDirectories"], ["/h/juno-vault"])
        self.assertEqual(block["allow"], ["Read(~/juno-vault/**)", "Edit(~/juno-vault/**)"])
        self.assertEqual(
            block["deny"],
            ["Edit(~/juno-vault/rules/**)", "Edit(~/juno-vault/.git/**)"],
        )

    def test_absolute_paths_outside_home(self) -> None:
        block = sm.build_block("/data/vault", "/h")
        self.assertEqual(block["allow"][0], "Read(//data/vault/**)")

    def test_home_prefix_is_component_wise(self) -> None:
        self.assertEqual(sm.permission_path("/hx/v", "/h"), "//hx/v")


class Merge(unittest.TestCase):
    block = sm.build_block("/data/vault", "/h")

    def test_adds_only_and_appends(self) -> None:
        data = realistic_settings()
        new, record = sm.merge(data, self.block)
        old_allow = data["permissions"]["allow"]
        self.assertEqual(new["permissions"]["allow"][: len(old_allow)], old_allow)
        self.assertEqual(new["permissions"]["allow"][len(old_allow) :], self.block["allow"])
        self.assertIs(new["autoMemoryEnabled"], False)
        self.assertEqual(new["hooks"], data["hooks"])
        self.assertEqual(record["created"], [])
        self.assertFalse("autoMemoryEnabled" in data)

    def test_second_merge_adds_nothing(self) -> None:
        once, _ = sm.merge(realistic_settings(), self.block)
        twice, record = sm.merge(once, self.block)
        self.assertEqual(once, twice)
        self.assertEqual(record["added"], {})
        self.assertFalse(record["autoMemoryEnabled"]["changed"])

    def test_creates_missing_keys_and_records_them(self) -> None:
        new, record = sm.merge({}, self.block)
        self.assertEqual(
            record["created"],
            [
                "permissions",
                "permissions.additionalDirectories",
                "permissions.allow",
                "permissions.deny",
            ],
        )
        self.assertEqual(sm.unmerge(new, record)[0], {})

    def test_previous_auto_memory_value_recorded(self) -> None:
        data = {"autoMemoryEnabled": True}
        new, record = sm.merge(data, self.block)
        self.assertEqual(
            record["autoMemoryEnabled"], {"present": True, "value": True, "changed": True}
        )
        self.assertEqual(sm.unmerge(new, record)[0], data)

    def test_wrong_types_refused(self) -> None:
        with self.assertRaises(sm.SettingsError):
            sm.merge({"permissions": []}, self.block)
        with self.assertRaises(sm.SettingsError):
            sm.merge({"permissions": {"allow": "x"}}, self.block)

    def test_roundtrip_for_every_variant(self) -> None:
        for name, raw in settings_variants().items():
            with self.subTest(name):
                data = sm.parse(raw)
                new, record = sm.merge(data, self.block)
                restored, notes = sm.unmerge(new, record)
                self.assertEqual(restored, data)
                self.assertEqual(notes, [])

    def test_unmerge_keeps_user_edits(self) -> None:
        new, record = sm.merge(realistic_settings(), self.block)
        new["permissions"]["allow"].append("Bash(make:*)")
        new["model"] = "haiku"
        restored, _ = sm.unmerge(new, record)
        self.assertIn("Bash(make:*)", restored["permissions"]["allow"])
        self.assertEqual(restored["model"], "haiku")
        self.assertNotIn("autoMemoryEnabled", restored)

    def test_unmerge_notes_changed_auto_memory(self) -> None:
        new, record = sm.merge({}, self.block)
        new["autoMemoryEnabled"] = True
        restored, notes = sm.unmerge(new, record)
        self.assertTrue(restored["autoMemoryEnabled"])
        self.assertEqual(len(notes), 1)

    def test_merge_records_keeps_first_auto_memory_change(self) -> None:
        first = {
            "added": {"allow": ["a"]},
            "created": ["permissions"],
            "autoMemoryEnabled": {"present": False, "changed": True},
        }
        later = {
            "added": {"allow": ["a", "b"]},
            "created": [],
            "autoMemoryEnabled": {"present": True, "value": False, "changed": False},
        }
        merged = sm.merge_records(first, later)
        self.assertEqual(merged["added"]["allow"], ["a", "b"])
        self.assertTrue(merged["autoMemoryEnabled"]["changed"])


class Serialisation(unittest.TestCase):
    def test_parse_rejects_non_strict_json(self) -> None:
        for raw in (b'{"a": 1,}', b"// c\n{}", b"[1]", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(sm.SettingsError):
                sm.parse(raw)

    def test_parse_empty_is_empty_object(self) -> None:
        self.assertEqual(sm.parse(b" \n"), {})

    def test_indent_detection(self) -> None:
        self.assertEqual(sm.detect_indent(b'{\n    "a": 1\n}\n'), 4)
        self.assertEqual(sm.detect_indent(b'{\n\t"a": 1\n}\n'), "\t")
        self.assertEqual(sm.detect_indent(b'{"a":1}'), 2)

    def test_dumps_keeps_unicode_and_newline_choice(self) -> None:
        self.assertEqual(sm.dumps({"a": "é"}, 2, False), '{\n  "a": "é"\n}'.encode())
        self.assertTrue(sm.dumps({}, 2).endswith(b"\n"))
        self.assertEqual(json.loads(sm.dumps({"a": 1}, "\t")), {"a": 1})


if __name__ == "__main__":
    unittest.main()
