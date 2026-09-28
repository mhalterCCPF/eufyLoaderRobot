import unittest

import controller
import expected_timing


class ExpectedTimingTests(unittest.TestCase):
    def test_expected_cycle_seconds_matches_hand_computed_value(self):
        axis_state = expected_timing.new_axis_state()
        total = expected_timing.expected_cycle_seconds(3, [0.0] * controller.N_SHELVES, axis_state)
        self.assertAlmostEqual(total, 105.9)
        self.assertEqual(axis_state, {"PM": controller.OFFSET_HOME, "LM": 90.0, "RM": 20.0})

    def test_axis_state_continues_across_cycles(self):
        axis_state = expected_timing.new_axis_state()
        expected_timing.expected_cycle_seconds(3, [0.0] * controller.N_SHELVES, axis_state)
        lm_after_first_cycle = axis_state["LM"]

        second = expected_timing.expected_cycle_seconds(4, [0.0] * controller.N_SHELVES, dict(axis_state))
        with_fresh_state = expected_timing.expected_cycle_seconds(
            4, [0.0] * controller.N_SHELVES, expected_timing.new_axis_state()
        )
        self.assertNotEqual(lm_after_first_cycle, 0.0)
        self.assertNotEqual(second, with_fresh_state)

    def test_expected_home_seconds_resets_axis_state(self):
        axis_state = {"PM": 12.0, "LM": -30.0, "RM": 8.0}
        expected_timing.expected_home_seconds(axis_state)
        self.assertEqual(axis_state, {"PM": 0.0, "LM": 0.0, "RM": 0.0})


if __name__ == "__main__":
    unittest.main()
