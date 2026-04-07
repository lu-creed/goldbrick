import unittest

from app.services.custom_indicator_eval import eval_expression, parse_and_validate_expr


class CustomIndicatorEvalTest(unittest.TestCase):
    def test_parse_and_eval_simple(self) -> None:
        allowed = frozenset({"close", "MA5", "open"})
        tree = parse_and_validate_expr("(close - open) / open * 100", allowed)
        v = eval_expression(tree, {"close": 11.0, "open": 10.0})
        self.assertAlmostEqual(v, 10.0)

    def test_reject_unknown_name(self) -> None:
        allowed = frozenset({"close"})
        with self.assertRaises(ValueError):
            parse_and_validate_expr("close + foo", allowed)

    def test_reject_call(self) -> None:
        allowed = frozenset({"close"})
        with self.assertRaises(ValueError):
            parse_and_validate_expr("abs(close)", allowed)


if __name__ == "__main__":
    unittest.main()
