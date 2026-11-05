"""No-network regressions for the independent banner timer."""
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import guild_banner_timer_runner as runner

class TimerTests(unittest.TestCase):
    def test_korean_slots_retry_every_five_minutes(self):
        tz=ZoneInfo("Asia/Seoul")
        self.assertIsNone(runner.active_slot(datetime(2026,11,5,7,59,tzinfo=tz)))
        self.assertEqual(runner.active_slot(datetime(2026,11,5,8,0,tzinfo=tz)),"2026-11-05 08:00")
        self.assertEqual(runner.active_slot(datetime(2026,11,5,8,55,tzinfo=tz)),"2026-11-05 08:00")
        self.assertEqual(runner.active_slot(datetime(2026,11,5,9,25,tzinfo=tz)),"2026-11-05 08:00")
        self.assertIsNone(runner.active_slot(datetime(2026,11,5,9,30,tzinfo=tz)))
        self.assertEqual(runner.active_slot(datetime(2026,11,5,18,0,tzinfo=tz)),"2026-11-05 18:00")
        self.assertEqual(runner.active_slot(datetime(2026,11,5,19,15,tzinfo=tz)),"2026-11-05 18:00")
        self.assertIsNone(runner.active_slot(datetime(2026,11,5,19,30,tzinfo=tz)))

    def test_completed_slot_never_reapplies_a_banner(self):
        state={"last_slot":"2026-11-05 08:00","info_card_pending":False}
        with patch.object(runner,"active_slot",return_value="2026-11-05 08:00"), \
             patch.object(runner,"read_state",return_value=state), \
             patch.object(runner,"rotation_selection") as select, \
             patch.dict("os.environ",{"DISCORD_BOT_TOKEN":"test","DISCORD_GUILD_ID":"123"}):
            runner.main()
            select.assert_not_called()

    def test_pending_card_repairs_without_reapplying_banner(self):
        state={"last_slot":"2026-11-05 08:00","info_card_pending":True}
        with patch.object(runner,"active_slot",return_value="2026-11-05 08:00"), \
             patch.object(runner,"read_state",return_value=state), \
             patch.object(runner,"sync_existing_card",return_value="edited") as repair, \
             patch.object(runner,"change_guild_banner") as change, \
             patch.dict("os.environ",{"DISCORD_BOT_TOKEN":"test","DISCORD_GUILD_ID":"123"}):
            runner.main()
            repair.assert_called_once()
            change.assert_not_called()

if __name__=="__main__":
    unittest.main()
