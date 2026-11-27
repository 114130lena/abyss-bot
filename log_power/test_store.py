import unittest,tempfile
from pathlib import Path
from log_power.store import Store
class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s=Store(Path(self.tmp.name)/"test.sqlite")
    def test_rewards(self):
        self.assertTrue(self.s.attend("1"))
        self.assertFalse(self.s.attend("1"))
        self.s.voice("1",1200,"v1")
        self.s.voice("1",600,"v2")
        self.s.voice("1",600,"v2")
        self.assertEqual(self.s.balance("1"),(1500,1800))
    def test_settle(self):
        self.s.grant(["1","2"],10000,"g","event","admin")
        p=self.s.create("q","a","b",None)
        self.s.bet(p,"1",0,8000,"a",0)
        self.s.bet(p,"2",1,2000,"b",0)
        self.s.close(p)
        self.s.settle(p,1,100,"admin")
        self.assertEqual(self.s.balance("2")[0],18000)
        self.assertEqual(self.s.balance("1")[0],2000)
        with self.assertRaises(ValueError): self.s.settle(p,1,100,"admin")
    def test_refund(self):
        self.s.attend("1")
        p=self.s.create("q","a","b",None)
        self.s.bet(p,"1",0,500,"a",0)
        self.s.settle(p,None,100,"admin")
        self.assertEqual(self.s.balance("1")[0],1000)
if __name__=="__main__": unittest.main()
