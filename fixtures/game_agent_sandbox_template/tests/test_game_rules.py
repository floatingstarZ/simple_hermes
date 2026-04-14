import unittest
from pathlib import Path


class CoinCatcherRulesTest(unittest.TestCase):
    def test_collect_coin_awards_ten_points(self) -> None:
        source = Path("src/game.js").read_text(encoding="utf-8")
        self.assertIn("score += 10;", source)
        self.assertNotIn("score += 1;", source)


if __name__ == "__main__":
    unittest.main()
