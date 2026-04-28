"""combiner 模块：校验 / 求值 / 字符串↔树 的单元测试。"""
from __future__ import annotations

import unittest

from app.services.combiner import (
    combiner_from_str,
    combiner_to_str,
    eval_combiner,
    validate_combiner,
)


class ValidateCombinerTest(unittest.TestCase):
    def test_leaf_ok(self):
        validate_combiner({"ref": "G1"}, ["G1", "G2"])

    def test_empty_groups_rejected(self):
        with self.assertRaises(ValueError):
            validate_combiner({"ref": "G1"}, [])

    def test_ref_not_in_groups(self):
        with self.assertRaises(ValueError):
            validate_combiner({"ref": "G9"}, ["G1"])

    def test_ref_and_op_conflict(self):
        with self.assertRaises(ValueError):
            validate_combiner({"ref": "G1", "op": "AND", "args": []}, ["G1"])

    def test_missing_both_ref_and_op(self):
        with self.assertRaises(ValueError):
            validate_combiner({}, ["G1"])

    def test_and_requires_two_children(self):
        with self.assertRaises(ValueError):
            validate_combiner({"op": "AND", "args": [{"ref": "G1"}]}, ["G1", "G2"])

    def test_not_requires_exactly_one_child(self):
        with self.assertRaises(ValueError):
            validate_combiner(
                {"op": "NOT", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                ["G1", "G2"],
            )

    def test_nested_ok(self):
        validate_combiner(
            {"op": "OR", "args": [
                {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                {"ref": "G3"},
            ]},
            ["G1", "G2", "G3"],
        )

    def test_invalid_op_rejected(self):
        with self.assertRaises(ValueError):
            validate_combiner(
                {"op": "XOR", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                ["G1", "G2"],
            )

    def test_depth_limit(self):
        # 构造超过 16 层的嵌套
        tree = {"ref": "G1"}
        for _ in range(20):
            tree = {"op": "NOT", "args": [tree]}
        with self.assertRaises(ValueError):
            validate_combiner(tree, ["G1"])


class EvalCombinerTest(unittest.TestCase):
    def test_leaf(self):
        self.assertTrue(eval_combiner({"ref": "G1"}, {"G1": True}))
        self.assertFalse(eval_combiner({"ref": "G1"}, {"G1": False}))

    def test_and_short_circuit(self):
        # 第二个 ref 缺失也不报错（短路）
        tree = {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G_MISSING"}]}
        self.assertFalse(eval_combiner(tree, {"G1": False}))

    def test_or_short_circuit(self):
        tree = {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G_MISSING"}]}
        self.assertTrue(eval_combiner(tree, {"G1": True}))

    def test_not(self):
        self.assertTrue(eval_combiner({"op": "NOT", "args": [{"ref": "G1"}]}, {"G1": False}))

    def test_nested_truth_table(self):
        # (G1 AND G2) OR G3
        tree = {"op": "OR", "args": [
            {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
            {"ref": "G3"},
        ]}
        self.assertTrue(eval_combiner(tree, {"G1": True, "G2": True, "G3": False}))
        self.assertFalse(eval_combiner(tree, {"G1": True, "G2": False, "G3": False}))
        self.assertTrue(eval_combiner(tree, {"G1": False, "G2": False, "G3": True}))

    def test_missing_ref_raises(self):
        with self.assertRaises(ValueError):
            eval_combiner({"ref": "G1"}, {})


class StringRoundTripTest(unittest.TestCase):
    def test_single_ref(self):
        self.assertEqual(combiner_to_str({"ref": "G1"}), "G1")

    def test_and(self):
        tree = {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]}
        self.assertEqual(combiner_to_str(tree), "G1 AND G2")

    def test_or(self):
        tree = {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G2"}]}
        self.assertEqual(combiner_to_str(tree), "G1 OR G2")

    def test_or_contains_and_no_parens(self):
        # AND 优先级高于 OR，当 AND 在 OR 内部时不需要括号
        tree = {"op": "OR", "args": [
            {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
            {"ref": "G3"},
        ]}
        self.assertEqual(combiner_to_str(tree), "G1 AND G2 OR G3")

    def test_and_contains_or_needs_parens(self):
        # OR 在 AND 内部时需要括号
        tree = {"op": "AND", "args": [
            {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G2"}]},
            {"ref": "G3"},
        ]}
        self.assertEqual(combiner_to_str(tree), "(G1 OR G2) AND G3")

    def test_not(self):
        tree = {"op": "NOT", "args": [{"ref": "G1"}]}
        self.assertEqual(combiner_to_str(tree), "NOT G1")

    def test_parse_single(self):
        self.assertEqual(
            combiner_from_str("G1", ["G1"]),
            {"ref": "G1"},
        )

    def test_parse_simple_and(self):
        tree = combiner_from_str("G1 AND G2", ["G1", "G2"])
        self.assertEqual(tree, {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]})

    def test_parse_case_insensitive(self):
        tree = combiner_from_str("g1 and g2", ["g1", "g2"])
        self.assertEqual(tree, {"op": "AND", "args": [{"ref": "g1"}, {"ref": "g2"}]})

    def test_parse_precedence(self):
        # G1 AND G2 OR G3  =>  (G1 AND G2) OR G3
        tree = combiner_from_str("G1 AND G2 OR G3", ["G1", "G2", "G3"])
        self.assertEqual(
            tree,
            {"op": "OR", "args": [
                {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                {"ref": "G3"},
            ]},
        )

    def test_parse_parens_override_precedence(self):
        # (G1 OR G2) AND G3
        tree = combiner_from_str("(G1 OR G2) AND G3", ["G1", "G2", "G3"])
        self.assertEqual(
            tree,
            {"op": "AND", "args": [
                {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                {"ref": "G3"},
            ]},
        )

    def test_parse_not(self):
        tree = combiner_from_str("NOT G1 AND G2", ["G1", "G2"])
        self.assertEqual(
            tree,
            {"op": "AND", "args": [
                {"op": "NOT", "args": [{"ref": "G1"}]},
                {"ref": "G2"},
            ]},
        )

    def test_parse_double_not(self):
        tree = combiner_from_str("NOT NOT G1", ["G1"])
        self.assertEqual(
            tree,
            {"op": "NOT", "args": [{"op": "NOT", "args": [{"ref": "G1"}]}]},
        )

    def test_parse_unknown_group_rejected(self):
        with self.assertRaises(ValueError):
            combiner_from_str("G1 AND G9", ["G1", "G2"])

    def test_parse_unclosed_paren(self):
        with self.assertRaises(ValueError):
            combiner_from_str("(G1 AND G2", ["G1", "G2"])

    def test_parse_illegal_char(self):
        with self.assertRaises(ValueError):
            combiner_from_str("G1 @ G2", ["G1", "G2"])

    def test_parse_trailing_garbage(self):
        with self.assertRaises(ValueError):
            combiner_from_str("G1 AND G2 G3", ["G1", "G2", "G3"])


if __name__ == "__main__":
    unittest.main()
