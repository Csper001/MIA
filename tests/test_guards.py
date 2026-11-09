import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import app as mia


class GuardTests(unittest.TestCase):
    def setUp(self):
        mia._usage_date = ""
        mia._usage_counts.update(advice=0, makeover=0)
        mia._inflight.update(advice=0, makeover=0)

    def test_makeover_daily_limit(self):
        async def run():
            with patch.dict(os.environ, {"MIA_DAILY_MAKEOVER_LIMIT": "1"}):
                await mia.reserve_generation("makeover")
                await mia.release_generation("makeover")
                with self.assertRaises(HTTPException) as err:
                    await mia.reserve_generation("makeover")
                self.assertEqual(err.exception.status_code, 429)
        asyncio.run(run())

    def test_concurrent_limit(self):
        async def run():
            with patch.dict(os.environ, {"MIA_MAX_CONCURRENT_REQUESTS": "1"}):
                await mia.reserve_generation("advice")
                try:
                    with self.assertRaises(HTTPException) as err:
                        await mia.reserve_generation("makeover")
                    self.assertEqual(err.exception.status_code, 429)
                finally:
                    await mia.release_generation("advice")
                await mia.reserve_generation("makeover")
                await mia.release_generation("makeover")
        asyncio.run(run())

    def test_disabled_limit(self):
        async def run():
            with patch.dict(os.environ, {"MIA_DAILY_ADVICE_LIMIT": "0"}):
                with self.assertRaises(HTTPException) as err:
                    await mia.reserve_generation("advice")
                self.assertEqual(err.exception.status_code, 429)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
