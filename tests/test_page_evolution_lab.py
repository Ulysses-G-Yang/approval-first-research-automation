from __future__ import annotations

import unittest

from labs.page_evolution.run_lab import run_lab


class PageEvolutionLabTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_lab_exercises_all_bounded_paths_without_network(self) -> None:
        results = await run_lab()
        self.assertEqual([result.path for result in results], ["configured_selector", "adaptive_fixture", "candidate_selector"])
        self.assertEqual([result.value for result in results], ["Northwind notebook"] * 3)
        self.assertTrue(all(not result.network_access for result in results))
