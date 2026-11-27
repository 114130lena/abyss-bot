import json,time,unittest,os
from unittest.mock import patch,MagicMock
from nacl.signing import SigningKey
from banner_bookmark_interactions import process

class RouterTests(unittest.TestCase):
    def setUp(self):
        self.key=SigningKey.generate()
        self.env=patch.dict(os.environ,{"DISCORD_APPLICATION_PUBLIC_KEY":self.key.verify_key.encode().hex(),"DISCORD_GUILD_ID":"123"})
        self.env.start()
        self.addCleanup(self.env.stop)
    def call(self,data,stamp=None):
        body=json.dumps(data).encode()
        stamp=str(int(time.time()) if stamp is None else stamp)
        sig=self.key.sign(stamp.encode()+body).signature.hex()
        return process(body,sig,stamp)
    def payload(self,typ=3,channel="1575809466576932904"):
        return {"type":typ,"guild_id":"123","channel_id":channel,"data":{"custom_id":"lp:attendance"}}
    def test_ping(self):
        self.assertEqual(self.call({"type":1}),(200,{"type":1}))
    def test_routes_buttons_and_modal(self):
        with patch("banner_bookmark_interactions.urllib.request.urlopen") as fn:
            fn.return_value.__enter__.return_value.status=202
            for typ in (3,5):
                self.assertEqual(self.call(self.payload(typ)),(202,None))
            self.assertEqual(fn.call_count,2)
    def test_wrong_channel(self):
        with patch("banner_bookmark_interactions.urllib.request.urlopen") as fn:
            self.assertEqual(self.call(self.payload(channel="999"))[1]["data"]["flags"],64)
            fn.assert_not_called()
    def test_offline(self):
        with patch("banner_bookmark_interactions.urllib.request.urlopen",side_effect=OSError):
            status,body=self.call(self.payload())
            self.assertEqual(status,200)
            self.assertEqual(body["data"]["flags"],64)
    def test_stale(self):
        self.assertEqual(self.call(self.payload(),int(time.time())-100)[0],401)
    def test_invalid_signature(self):
        self.assertEqual(process(b"{}","00"*64,str(int(time.time())))[0],401)
    def test_existing_banner_permission_path(self):
        with patch("banner_bookmark_interactions.urllib.request.urlopen") as fn:
            status,body=self.call({"type":3,"guild_id":"123","data":{"custom_id":"abyss:banner:keep:x"},"member":{"permissions":"0","user":{"id":"2"}}})
            self.assertEqual(status,200)
            self.assertIn("권한",body["data"]["content"])
            fn.assert_not_called()

if __name__=="__main__": unittest.main()
