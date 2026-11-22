#!/usr/bin/env python3
"""폰 악성코드 점검기 (Android 전용, PC + USB/ADB 방식).

폰에 설치된 앱을 읽기 전용으로 조회해 의심 신호를 점수화하고 HTML 리포트를 만든다.
폰에는 아무것도 설치/변경하지 않으며, 데이터는 PC 밖으로 나가지 않는다.
표준 라이브러리만 사용한다.

주의: 이 도구는 '의심 신호'를 찾는 휴리스틱이다. 악성코드 여부를 확정하지 않는다.
"""
import argparse
import datetime
import html
import json
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 공식 스토어 설치 경로. 여기에 없으면 '출처 불명(사이드로드)'으로 본다.
TRUSTED_INSTALLERS = {
    "com.android.vending",            # Google Play
    "com.sec.android.app.samsungapps",  # Galaxy Store
    "com.amazon.venezia",             # Amazon Appstore
    "com.skt.skaf.A000Z00040",        # ONE store
}

SMS_PERMS = {
    "android.permission.READ_SMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.SEND_SMS",
    "android.permission.RECEIVE_MMS",
}

# (점수, 라벨) — 앱 단위 신호
W_BLOCKLIST = 10
W_ACCESSIBILITY = 3
W_DEVICE_ADMIN = 3
W_SIDELOAD = 2
W_SMS = 2
W_NOTIF_LISTENER = 1
W_OVERLAY = 1
W_INSTALL_PKGS = 1
W_BG_LOCATION = 1
W_CALL_LOG = 1

LEVEL_DANGER = 6
LEVEL_WARN = 3


class AdbError(RuntimeError):
    pass


# ---------------------------------------------------------------- ADB 계층

def find_adb():
    local = HERE / "platform-tools" / ("adb.exe" if sys.platform == "win32" else "adb")
    if local.exists():
        return str(local)
    return shutil.which("adb")


def make_runner(adb, serial=None):
    def run(*args, timeout=60):
        cmd = [adb] + (["-s", serial] if serial else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            raise AdbError("adb 응답 시간 초과: " + " ".join(args)) from e
        if p.returncode != 0:
            raise AdbError((p.stderr or p.stdout).strip() or "adb 오류")
        return p.stdout
    return run


def parse_devices(text):
    """`adb devices` 출력 -> [(serial, state)]"""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


# ---------------------------------------------------------------- 파서

def parse_packages(text):
    """`pm list packages -3 -i` -> {pkg: installer|None}"""
    res = {}
    for line in text.splitlines():
        m = re.match(r"\s*package:(\S+)(?:\s+installer=(\S+))?", line)
        if m:
            inst = m.group(2)
            res[m.group(1)] = None if inst in (None, "null") else inst
    return res


def parse_pkg_list(text):
    return {m.group(1) for m in re.finditer(r"package:(\S+)", text)}


def parse_components(value):
    """settings 값 'pkg/cls:pkg2/cls2' -> {pkg, pkg2}. 'null'/빈 값은 빈 집합."""
    value = (value or "").strip()
    if not value or value == "null":
        return set()
    pkgs = set()
    for comp in value.split(":"):
        comp = comp.strip()
        if "/" in comp:
            pkgs.add(comp.split("/", 1)[0])
    return pkgs


def parse_device_admins(text):
    return set(re.findall(r"admin=(?:ComponentInfo\{)?([\w.]+)/", text))


def parse_dumpsys_package(text):
    """dumpsys package <pkg> -> 권한/버전 정보"""
    requested = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "requested permissions:":
            base = len(line) - len(line.lstrip())
            for sub in lines[i + 1:]:
                if not sub.strip():
                    continue
                indent = len(sub) - len(sub.lstrip())
                if indent <= base:
                    break
                requested.add(sub.strip().split(":", 1)[0])
            break
    granted = set(re.findall(r"([\w.]+): granted=true", text))
    ver = re.search(r"versionName=(\S+)", text)
    first = re.search(r"firstInstallTime=(.+)", text)
    return {
        "requested": requested,
        "granted": granted,
        "version": ver.group(1) if ver else "",
        "first_install": first.group(1).strip() if first else "",
    }


def load_blocklist(path):
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


# ---------------------------------------------------------------- 판정

def assess(pkg, installer, info, ctx):
    """앱 하나의 (점수, [사유]) 반환. ctx: 기기 단위 신호 집합들."""
    score, reasons = 0, []

    if pkg in ctx["blocklist"]:
        score += W_BLOCKLIST
        reasons.append("사용자 차단 목록(blocklist)에 있는 앱")
    if pkg in ctx["accessibility"]:
        score += W_ACCESSIBILITY
        reasons.append("접근성 서비스 활성화됨 (화면 읽기·입력 가로채기에 악용 가능)")
    if pkg in ctx["device_admin"]:
        score += W_DEVICE_ADMIN
        reasons.append("기기 관리자 권한 보유 (삭제가 어려울 수 있음)")
    if installer not in TRUSTED_INSTALLERS:
        score += W_SIDELOAD
        reasons.append("공식 스토어가 아닌 경로로 설치됨 (설치 출처: %s)" % (installer or "알 수 없음"))
    if info["granted"] & SMS_PERMS:
        score += W_SMS
        reasons.append("문자 읽기/수신/발신 권한이 허용됨 (인증번호 탈취 가능)")
    if pkg in ctx["notif_listener"]:
        score += W_NOTIF_LISTENER
        reasons.append("알림 접근 허용됨 (알림 속 인증번호 열람 가능)")
    if "android.permission.SYSTEM_ALERT_WINDOW" in info["requested"]:
        score += W_OVERLAY
        reasons.append("다른 앱 위에 표시 권한 요청 (가짜 화면 덧씌우기에 악용 가능)")
    if "android.permission.REQUEST_INSTALL_PACKAGES" in info["requested"]:
        score += W_INSTALL_PKGS
        reasons.append("다른 앱 설치 권한 요청")
    if "android.permission.ACCESS_BACKGROUND_LOCATION" in info["granted"]:
        score += W_BG_LOCATION
        reasons.append("백그라운드 위치 접근 허용됨")
    if "android.permission.READ_CALL_LOG" in info["granted"]:
        score += W_CALL_LOG
        reasons.append("통화 기록 읽기 허용됨")
    return score, reasons


def level_of(score):
    if score >= LEVEL_DANGER:
        return "danger"
    if score >= LEVEL_WARN:
        return "warn"
    return "ok"


LEVEL_LABEL = {"danger": "위험", "warn": "주의", "ok": "양호"}


# ---------------------------------------------------------------- 수집

def collect(run, blocklist, progress=lambda i, n, p: None):
    third = parse_packages(run("shell", "pm", "list", "packages", "-3", "-i", timeout=120))
    system = parse_pkg_list(run("shell", "pm", "list", "packages", "-s", timeout=120))

    def setting(key):
        try:
            return run("shell", "settings", "get", "secure", key)
        except AdbError:
            return ""

    try:
        admins = parse_device_admins(run("shell", "dumpsys", "device_policy", timeout=120))
    except AdbError:
        admins = set()

    ctx = {
        "blocklist": blocklist,
        "accessibility": parse_components(setting("enabled_accessibility_services")),
        "notif_listener": parse_components(setting("enabled_notification_listeners")),
        "device_admin": admins,
    }

    results = []
    names = sorted(third)
    for i, pkg in enumerate(names, 1):
        progress(i, len(names), pkg)
        try:
            info = parse_dumpsys_package(run("shell", "dumpsys", "package", pkg, timeout=60))
        except AdbError:
            info = {"requested": set(), "granted": set(), "version": "", "first_install": ""}
        score, reasons = assess(pkg, third[pkg], info, ctx)
        results.append({
            "package": pkg, "installer": third[pkg], "version": info["version"],
            "first_install": info["first_install"], "score": score,
            "level": level_of(score), "reasons": reasons,
        })

    # 시스템 앱으로 위장한 접근성/관리자 앱: 3rd-party 목록에 없고 system 목록에도 없는 경우만 추가
    extra = (ctx["accessibility"] | ctx["device_admin"]) - set(third) - system
    for pkg in sorted(extra):
        score, reasons = assess(pkg, None, {"requested": set(), "granted": set()}, ctx)
        results.append({
            "package": pkg, "installer": None, "version": "", "first_install": "",
            "score": score, "level": level_of(score), "reasons": reasons,
        })

    results.sort(key=lambda r: (-r["score"], r["package"]))
    return results


# ---------------------------------------------------------------- 리포트

def summarize(results):
    s = {"danger": 0, "warn": 0, "ok": 0}
    for r in results:
        s[r["level"]] += 1
    return s


def render_console(results):
    s = summarize(results)
    lines = ["", "검사한 앱 %d개  |  위험 %d  주의 %d  양호 %d" % (len(results), s["danger"], s["warn"], s["ok"]), ""]
    for r in results:
        if r["level"] == "ok":
            continue
        lines.append("[%s] %s (점수 %d)" % (LEVEL_LABEL[r["level"]], r["package"], r["score"]))
        for why in r["reasons"]:
            lines.append("    - " + why)
    if s["danger"] + s["warn"] == 0:
        lines.append("의심 신호가 발견되지 않았습니다.")
    return "\n".join(lines)


HTML_TMPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>폰 점검 리포트</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--danger:#c62828;--warn:#b26a00;--ok:#2e7d32;--card:#f6f6f6}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#eee;--mut:#aaa;--line:#333;--card:#1e1e1e;--danger:#ef5350;--warn:#ffb74d;--ok:#81c784}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:16px;max-width:860px;margin:auto}
h1{font-size:20px}.mut{color:var(--mut)}.sum{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.pill{padding:6px 12px;border-radius:999px;background:var(--card);border:1px solid var(--line)}
.card{border:1px solid var(--line);border-left-width:5px;border-radius:8px;padding:10px 14px;margin:10px 0;background:var(--card)}
.card.danger{border-left-color:var(--danger)}.card.warn{border-left-color:var(--warn)}
.tag{font-weight:700}.danger .tag{color:var(--danger)}.warn .tag{color:var(--warn)}
code{word-break:break-all}ul{margin:6px 0 0 18px;padding:0}
.box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:14px 0}
</style></head><body>
<h1>폰 점검 리포트</h1>
<div class="mut">기기 %(device)s · %(when)s · 검사한 앱 %(total)d개</div>
<div class="sum"><span class="pill">위험 <b>%(danger)d</b></span><span class="pill">주의 <b>%(warn)d</b></span><span class="pill">양호 <b>%(ok)d</b></span></div>
%(cards)s
<div class="box"><b>의심 앱이 나왔다면</b><ol>
<li>설정 → 앱에서 해당 앱을 찾아 삭제합니다. '삭제' 버튼이 비활성이면 설정 → 보안 → 기기 관리자 앱(또는 접근성)에서 먼저 해제합니다.</li>
<li>삭제 후 Google 계정·금융앱 비밀번호를 변경하고, Play 스토어 → Play 프로텍트 검사를 실행합니다.</li>
<li>계속 의심되면 백업 후 초기화를 고려하세요.</li></ol></div>
<p class="mut">이 리포트는 권한·설치 경로 기반의 휴리스틱 점검이며 악성코드 여부를 확정하지 않습니다. 메신저·금융·원격제어 앱처럼 정상 앱도 높은 점수가 나올 수 있으니, 직접 설치한 기억이 있는 앱인지 확인하세요.</p>
</body></html>"""


def render_html(results, device, now=None):
    s = summarize(results)
    cards = []
    for r in results:
        if r["level"] == "ok":
            continue
        lis = "".join("<li>%s</li>" % html.escape(w) for w in r["reasons"])
        meta = " · ".join(x for x in [("v" + r["version"]) if r["version"] else "", r["first_install"]] if x)
        cards.append(
            '<div class="card %s"><span class="tag">%s</span> <code>%s</code> <span class="mut">점수 %d%s</span><ul>%s</ul></div>'
            % (r["level"], LEVEL_LABEL[r["level"]], html.escape(r["package"]), r["score"],
               (" · " + html.escape(meta)) if meta else "", lis))
    if not cards:
        cards.append('<div class="box">의심 신호가 발견되지 않았습니다.</div>')
    when = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M")
    return HTML_TMPL % {
        "device": html.escape(device), "when": when, "total": len(results),
        "danger": s["danger"], "warn": s["warn"], "ok": s["ok"], "cards": "\n".join(cards),
    }


# ---------------------------------------------------------------- 진입점

SETUP_HELP = """\
ADB를 찾을 수 없습니다. 아래 순서로 준비하세요.
 1. Android platform-tools(ADB)를 설치하거나 이 폴더의 platform-tools/ 에 풉니다.
 2. 폰: 설정 → 휴대전화 정보 → 빌드번호 7번 탭 → 개발자 옵션 → USB 디버깅 켜기
 3. USB로 PC에 연결하고 폰 화면의 'USB 디버깅 허용'을 승인합니다."""


def main(argv=None):
    ap = argparse.ArgumentParser(description="폰 악성코드 점검기 (Android, ADB)")
    ap.add_argument("--serial", help="여러 기기가 연결된 경우 대상 기기 시리얼")
    ap.add_argument("--out", default=str(Path.home() / "폰검사_리포트"), help="리포트 저장 폴더")
    ap.add_argument("--blocklist", default=str(HERE / "blocklist.txt"), help="차단할 패키지명 목록 파일")
    ap.add_argument("--no-open", action="store_true", help="리포트를 브라우저로 열지 않음")
    args = ap.parse_args(argv)

    adb = find_adb()
    if not adb:
        print(SETUP_HELP)
        return 2

    devices = parse_devices(make_runner(adb)("devices"))
    ready = [d for d in devices if d[1] == "device"]
    if args.serial:
        ready = [d for d in ready if d[0] == args.serial]
    if not ready:
        states = ", ".join("%s(%s)" % d for d in devices) or "없음"
        print("연결된 폰이 없습니다. 감지된 기기: %s\n폰의 'USB 디버깅 허용' 팝업을 확인하세요." % states)
        return 3
    if len(ready) > 1:
        print("기기가 여러 대입니다. --serial 로 지정하세요: " + ", ".join(d[0] for d in ready))
        return 4
    serial = ready[0][0]
    run = make_runner(adb, serial)

    def progress(i, n, pkg):
        print("\r[%d/%d] %-60s" % (i, n, pkg[:60]), end="", flush=True)

    results = collect(run, load_blocklist(args.blocklist), progress)
    print()
    print(render_console(results))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = out / ("리포트_%s.html" % stamp)
    report.write_text(render_html(results, serial), encoding="utf-8")
    (out / ("리포트_%s.json" % stamp)).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n리포트 저장: %s" % report)
    if not args.no_open:
        webbrowser.open(report.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
