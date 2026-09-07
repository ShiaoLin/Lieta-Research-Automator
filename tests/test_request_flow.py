import unittest

from lieta_automator.request_flow import (
    LoginRequired, ModelTimeout, PageState, RequestCoordinator, RetryPolicy, fetch_result,
)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RequestFlowTests(unittest.TestCase):
    def run_flow(self, scenario, *, recover_session=None, **options):
        self.clock = Clock()
        self.policy = RetryPolicy(**options)
        self.coordinator = RequestCoordinator(self.policy, clock=self.clock)
        self.submits = []
        self.logs = []
        return fetch_result(
            lambda: scenario(self.clock(), len(self.submits)),
            lambda: self.submits.append(self.clock()), self.coordinator, self.logs.append,
            clock=self.clock, sleep=self.clock.sleep,
            recover_session=recover_session,
        )

    def test_unauthorized_refreshes_before_accepting_a_chart_or_waiting_for_busy(self):
        refreshes = []

        def scenario(t, n):
            if n and not refreshes:
                return PageState(session_expired=True, busy=True, ready=True,
                                 matches=True, result_key="unauthorized-chart")
            return PageState(result_key="fresh" if n > 1 else "", ready=n > 1, matches=n > 1)

        result = self.run_flow(scenario, recover_session=lambda: refreshes.append(self.clock()))
        self.assertEqual(result.result_key, "fresh")
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(len(self.submits), 2)
        self.assertGreaterEqual(self.submits[1] - self.submits[0], 5)

    def test_unauthorized_present_before_first_submit_is_refreshed(self):
        refreshes = []
        self.run_flow(
            lambda t, n: PageState(session_expired=not refreshes,
                                   result_key="fresh" if n else "", ready=bool(n), matches=bool(n)),
            recover_session=lambda: refreshes.append(self.clock()),
        )
        self.assertEqual(refreshes, [0])
        self.assertEqual(len(self.submits), 1)

    def test_repeated_unauthorized_stops_after_two_refreshes(self):
        refreshes = []
        with self.assertRaises(LoginRequired):
            self.run_flow(lambda t, n: PageState(session_expired=True),
                          recover_session=lambda: refreshes.append(self.clock()))
        self.assertEqual(len(refreshes), 2)
        self.assertEqual(self.submits, [])

    def test_refresh_does_not_reset_the_attempt_budget(self):
        refreshes = []
        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: PageState(session_expired=n > len(refreshes)),
                          recover_session=lambda: refreshes.append(self.clock()),
                          max_attempts=1, ticker_timeout=60)
        self.assertEqual(len(self.submits), 1)

    def test_refresh_does_not_reset_the_time_budget(self):
        def refresh():
            self.clock.sleep(9)

        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: PageState(session_expired=bool(n) and t < 9),
                          recover_session=refresh, ticker_timeout=10)
        self.assertEqual(self.clock(), 10)

    def test_login_page_after_refresh_stops_without_another_submit(self):
        refreshes = []
        with self.assertRaises(LoginRequired):
            self.run_flow(lambda t, n: PageState(session_expired=bool(n), authenticated=not refreshes),
                          recover_session=lambda: refreshes.append(self.clock()))
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(self.submits, [0])

    def test_chart_left_after_refresh_is_not_accepted_as_a_new_result(self):
        refreshes = []
        def scenario(t, n):
            if n and not refreshes:
                return PageState(session_expired=True)
            return PageState(result_key="old", ready=True, matches=True)
        with self.assertRaises(ModelTimeout):
            self.run_flow(scenario, recover_session=lambda: refreshes.append(self.clock()),
                          max_attempts=2, response_timeout=2, ticker_timeout=30)
        self.assertEqual(len(self.submits), 2)

    def test_old_chart_is_never_success(self):
        old = PageState(result_key="old", matches=True, ready=True)
        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: old, max_attempts=2, response_timeout=3, ticker_timeout=60)
        self.assertEqual(len(self.submits), 2)

    def test_new_chart_for_wrong_ticker_is_never_success(self):
        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: PageState(result_key=str(n), matches=False, ready=True),
                          max_attempts=1, response_timeout=2, ticker_timeout=30)

    def test_busy_response_can_take_longer_than_response_timeout(self):
        def scenario(t, n):
            if not n:
                return PageState()
            if t < 120:
                return PageState(busy=True)
            return PageState(result_key="new", matches=True, ready=True)
        result = self.run_flow(scenario)
        self.assertEqual(result.result_key, "new")
        self.assertEqual(self.submits, [0])
        self.assertGreaterEqual(self.clock(), 121)

    def test_error_backoff_grows_and_has_a_cap(self):
        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: PageState(errors=frozenset({f"toast-{n}"}) if n else frozenset()),
                          initial_backoff=10, maximum_backoff=20, max_attempts=4, ticker_timeout=100)
        self.assertEqual(self.submits, [0, 10.5, 31, 51.5])

    def test_late_result_during_backoff_avoids_resubmit(self):
        def scenario(t, n):
            if not n:
                return PageState()
            return PageState(result_key="new" if t >= 4 else "", matches=t >= 4, ready=t >= 4,
                             errors=frozenset({"toast"}))
        result = self.run_flow(scenario)
        self.assertEqual(result.result_key, "new")
        self.assertEqual(self.submits, [0])

    def test_previous_toast_does_not_fail_the_next_attempt(self):
        def scenario(t, n):
            return PageState(errors=frozenset({"old-toast"}), result_key="new" if n else "old",
                             matches=bool(n), ready=bool(n))
        self.run_flow(scenario)
        self.assertEqual(self.submits, [0])
        self.assertFalse(any("冷卻" in message for message in self.logs))

    def test_busy_forever_stops_at_overall_budget(self):
        with self.assertRaises(ModelTimeout):
            self.run_flow(lambda t, n: PageState(busy=bool(n)), ticker_timeout=10)
        self.assertEqual(self.submits, [0])
        self.assertEqual(self.clock(), 10)
        self.assertGreater(self.coordinator.next_allowed, self.clock())

    def test_error_takes_priority_over_new_result_in_same_snapshot(self):
        def scenario(t, n):
            if not n:
                return PageState()
            return PageState(result_key="new", matches=True, ready=True, errors=frozenset({"error"}))
        self.run_flow(scenario)
        self.assertTrue(any("網站顯示" in message for message in self.logs))
        self.assertGreaterEqual(self.clock(), 2)

    def test_logout_stops_without_resubmitting(self):
        with self.assertRaises(LoginRequired):
            self.run_flow(lambda t, n: PageState(authenticated=not n))
        self.assertEqual(self.submits, [0])

    def test_shared_cooldown_survives_a_change_of_ticker(self):
        clock = Clock()
        coordinator = RequestCoordinator(clock=clock)
        coordinator.failed()
        submits = []
        result = fetch_result(
            lambda: PageState(result_key="new" if submits else "", matches=bool(submits), ready=bool(submits)),
            lambda: submits.append(clock()), coordinator, lambda message: None,
            clock=clock, sleep=clock.sleep,
        )
        self.assertEqual(submits, [10])
        self.assertEqual(result.result_key, "new")


if __name__ == "__main__":
    unittest.main()
