import datetime
import tempfile
import unittest
from pathlib import Path

import install_desktop
import scan

PKG_LIST = """package:com.good.app  installer=com.android.vending
package:com.shady.spy  installer=null
package:com.chat.app  installer=com.android.vending
"""

DUMPSYS_SPY = """Packages:
  Package [com.shady.spy] (abc):
    versionName=1.2
    firstInstallTime=2026-01-02 10:00:00
    requested permissions:
      android.permission.READ_SMS
      android.permission.SYSTEM_ALERT_WINDOW: restricted=false
      android.permission.INTERNET
    install permissions:
      android.permission.INTERNET: granted=true
    User 0: ceDataInode=1
      runtime permissions:
        android.permission.READ_SMS: granted=true, flags=[]
"""

DUMPSYS_GOOD = """    versionName=3.0
    requested permissions:
      android.permission.INTERNET
    install permissions:
      android.permission.INTERNET: granted=true
"""


def fake_run(spy_extra=True):
    admin_out = "Enabled Device Admins:\n  admin=ComponentInfo{com.shady.spy/com.shady.Admin}\n"
    table = {
        ("shell", "pm", "list", "packages", "-3", "-i"): PKG_LIST,
        ("shell", "pm", "list", "packages", "-s"): "package:android\npackage:com.android.systemui\n",
        ("shell", "settings", "get", "secure", "enabled_accessibility_services"):
            "com.shady.spy/com.shady.Acc:com.android.talkback/x\n",
        ("shell", "settings", "get", "secure", "enabled_notification_listeners"): "null\n",
        ("shell", "dumpsys", "device_policy"): admin_out,
        ("shell", "dumpsys", "package", "com.shady.spy"): DUMPSYS_SPY,
        ("shell", "dumpsys", "package", "com.good.app"): DUMPSYS_GOOD,
        ("shell", "dumpsys", "package", "com.chat.app"): DUMPSYS_GOOD,
    }

    def run(*args, timeout=60):
        return table[args]
    return run


class ParserTests(unittest.TestCase):
    def test_parse_packages(self):
        r = scan.parse_packages(PKG_LIST)
        self.assertEqual(r["com.good.app"], "com.android.vending")
        self.assertIsNone(r["com.shady.spy"])

    def test_parse_packages_without_installer(self):
        self.assertEqual(scan.parse_packages("package:a.b\n"), {"a.b": None})

    def test_parse_components(self):
        self.assertEqual(scan.parse_components("a.b/.C:d.e/.F"), {"a.b", "d.e"})
        self.assertEqual(scan.parse_components("null"), set())
        self.assertEqual(scan.parse_components(""), set())

    def test_parse_device_admins(self):
        self.assertEqual(scan.parse_device_admins("admin=ComponentInfo{x.y/z.W}"), {"x.y"})
        self.assertEqual(scan.parse_device_admins("admin=x.y/.W"), {"x.y"})

    def test_parse_dumpsys(self):
        d = scan.parse_dumpsys_package(DUMPSYS_SPY)
        self.assertIn("android.permission.READ_SMS", d["requested"])
        self.assertIn("android.permission.SYSTEM_ALERT_WINDOW", d["requested"])
        self.assertIn("android.permission.READ_SMS", d["granted"])
        self.assertEqual(d["version"], "1.2")
        self.assertEqual(d["first_install"], "2026-01-02 10:00:00")

    def test_parse_devices(self):
        t = "List of devices attached\nabc123\tdevice\nxyz\tunauthorized\n\n"
        self.assertEqual(scan.parse_devices(t), [("abc123", "device"), ("xyz", "unauthorized")])


class AssessTests(unittest.TestCase):
    def ctx(self, **kw):
        c = {"blocklist": set(), "accessibility": set(), "notif_listener": set(), "device_admin": set()}
        c.update(kw)
        return c

    def info(self, requested=(), granted=()):
        return {"requested": set(requested), "granted": set(granted)}

    def test_clean_store_app_is_ok(self):
        s, why = scan.assess("a", "com.android.vending", self.info(), self.ctx())
        self.assertEqual((s, why, scan.level_of(s)), (0, [], "ok"))

    def test_sideload_alone_is_ok_level(self):
        s, _ = scan.assess("a", None, self.info(), self.ctx())
        self.assertEqual(s, scan.W_SIDELOAD)
        self.assertEqual(scan.level_of(s), "ok")

    def test_blocklist_is_danger(self):
        s, _ = scan.assess("a", "com.android.vending", self.info(), self.ctx(blocklist={"a"}))
        self.assertEqual(scan.level_of(s), "danger")

    def test_sideload_accessibility_sms_is_danger(self):
        s, why = scan.assess("a", None, self.info(granted=["android.permission.READ_SMS"]),
                             self.ctx(accessibility={"a"}))
        self.assertEqual(s, scan.W_SIDELOAD + scan.W_ACCESSIBILITY + scan.W_SMS)
        self.assertEqual(scan.level_of(s), "danger")
        self.assertEqual(len(why), 3)

    def test_level_boundaries(self):
        self.assertEqual(scan.level_of(2), "ok")
        self.assertEqual(scan.level_of(3), "warn")
        self.assertEqual(scan.level_of(5), "warn")
        self.assertEqual(scan.level_of(6), "danger")


class CollectTests(unittest.TestCase):
    def test_collect_ranks_spy_first_and_skips_system(self):
        res = scan.collect(fake_run(), set())
        self.assertEqual(res[0]["package"], "com.shady.spy")
        self.assertEqual(res[0]["level"], "danger")
        names = [r["package"] for r in res]
        self.assertEqual(sorted(set(names) - {"com.android.talkback"}),
                         ["com.chat.app", "com.good.app", "com.shady.spy"])

    def test_extra_accessibility_non_system_is_added(self):
        run = fake_run()
        res = scan.collect(run, set())
        # talkback 은 3rd-party 목록에도 system 목록에도 없으므로 추가 검사 대상
        self.assertIn("com.android.talkback", [r["package"] for r in res])

    def test_summary_and_reports(self):
        res = scan.collect(fake_run(), set())
        s = scan.summarize(res)
        self.assertEqual(s["danger"], 1)
        console = scan.render_console(res)
        self.assertIn("com.shady.spy", console)
        page = scan.render_html(res, "<dev>", now=datetime.datetime(2026, 11, 22, 9, 0))
        self.assertIn("2026-11-22 09:00", page)
        self.assertIn("&lt;dev&gt;", page)
        self.assertNotIn("<dev>", page)

    def test_no_findings_message(self):
        res = [{"package": "a", "installer": "com.android.vending", "version": "", "first_install": "",
                "score": 0, "level": "ok", "reasons": []}]
        self.assertIn("발견되지 않았습니다", scan.render_html(res, "d"))


class WirelessTests(unittest.TestCase):
    def test_is_termux(self):
        self.assertTrue(scan.is_termux({"PREFIX": "/data/data/com.termux/files/usr"}))
        self.assertFalse(scan.is_termux({"PREFIX": "/usr"}))
        self.assertFalse(scan.is_termux({}))

    def test_default_out(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            termux = {"PREFIX": "/data/data/com.termux/files/usr"}
            self.assertEqual(scan.default_out(termux, home), home / "폰검사_리포트")
            (home / "storage" / "downloads").mkdir(parents=True)
            self.assertEqual(scan.default_out(termux, home), home / "storage" / "downloads" / "폰검사_리포트")
            self.assertEqual(scan.default_out({}, home), home / "폰검사_리포트")

    def test_pair_success_and_failure(self):
        scan.adb_pair(lambda *a: "Successfully paired to localhost:4000 [guid=x]\n", "localhost:4000", "123456")
        with self.assertRaises(scan.AdbError):
            scan.adb_pair(lambda *a: "Failed: Wrong password or password is not set\n", "localhost:4000", "000000")

    def test_connect_success_and_failure(self):
        scan.adb_connect(lambda *a: "connected to localhost:5555\n", "localhost:5555")
        scan.adb_connect(lambda *a: "already connected to localhost:5555\n", "localhost:5555")
        for bad in ("failed to connect to 'localhost:5555': Connection refused\n",
                    "cannot connect to localhost:5555: Connection refused\n", ""):
            with self.assertRaises(scan.AdbError):
                scan.adb_connect(lambda *a, o=bad: o, "localhost:5555")

    def test_open_report_termux_without_opener_returns_false(self):
        termux = {"PREFIX": "/data/data/com.termux/files/usr"}
        orig = scan.shutil.which
        scan.shutil.which = lambda name: None
        try:
            self.assertFalse(scan.open_report("/tmp/x.html", termux))
        finally:
            scan.shutil.which = orig


class BlocklistTests(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.txt"
            p.write_text("# c\ncom.a.b  # x\n\ncom.c.d\n", encoding="utf-8")
            self.assertEqual(scan.load_blocklist(p), {"com.a.b", "com.c.d"})
            self.assertEqual(scan.load_blocklist(Path(d) / "none.txt"), set())


class InstallTests(unittest.TestCase):
    def test_install_creates_launchers_and_keeps_blocklist(self):
        with tempfile.TemporaryDirectory() as d:
            desk = Path(d)
            dest = install_desktop.install(desk)
            self.assertTrue((dest / "scan.py").exists())
            self.assertTrue((dest / "폰검사.bat").exists())
            self.assertTrue((desk / "폰검사 실행.bat").exists())
            (dest / "blocklist.txt").write_text("com.mine\n", encoding="utf-8")
            install_desktop.install(desk)
            self.assertEqual((dest / "blocklist.txt").read_text(encoding="utf-8"), "com.mine\n")

    def test_desktop_dir_prefers_onedrive(self):
        with tempfile.TemporaryDirectory() as d:
            od = Path(d) / "OneDrive"
            (od / "Desktop").mkdir(parents=True)
            home = Path(d) / "home"
            (home / "Desktop").mkdir(parents=True)
            self.assertEqual(install_desktop.desktop_dir({"OneDrive": str(od)}, home), od / "Desktop")
            self.assertEqual(install_desktop.desktop_dir({}, home), home / "Desktop")
            with self.assertRaises(FileNotFoundError):
                install_desktop.desktop_dir({}, Path(d) / "nohome")


if __name__ == "__main__":
    unittest.main()
