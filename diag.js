// 태블릿 점검기 - 판정 로직 (브라우저/Node 공용, DOM 의존 없음)
(function (root) {
  var OK = 'ok', WARN = 'warn', BAD = 'bad', NA = 'na', PENDING = 'pending';
  function r(status, msg) { return { status: status, msg: msg }; }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  function gradeBattery(level, charging) {
    if (!isNum(level)) return r(NA, '이 브라우저에서는 배터리 정보를 읽을 수 없습니다');
    var pct = Math.round(level * 100);
    var state = charging ? '충전 중' : '방전 중';
    if (!charging && pct <= 10) return r(BAD, pct + '% (' + state + ') - 배터리 부족');
    if (!charging && pct <= 20) return r(WARN, pct + '% (' + state + ') - 충전 권장');
    return r(OK, pct + '% (' + state + ')');
  }

  function gradeStorage(usage, quota) {
    if (!isNum(usage) || !isNum(quota) || quota <= 0) return r(NA, '저장공간 정보를 읽을 수 없습니다');
    var ratio = usage / quota;
    var msg = fmtBytes(usage) + ' / ' + fmtBytes(quota) + ' (' + Math.round(ratio * 100) + '%, 브라우저 할당 기준)';
    if (ratio > 0.9) return r(BAD, msg + ' - 공간 부족');
    if (ratio > 0.75) return r(WARN, msg + ' - 여유 적음');
    return r(OK, msg);
  }

  function gradeMemory(gb) {
    if (!isNum(gb)) return r(NA, '이 브라우저에서는 메모리 정보를 읽을 수 없습니다');
    if (gb < 2) return r(BAD, gb + 'GB 이하 - 매우 부족');
    if (gb < 3) return r(WARN, gb + 'GB - 다소 부족');
    return r(OK, gb + 'GB 이상' + (gb >= 8 ? ' (브라우저가 최대 8GB로 표시)' : ''));
  }

  function gradeNetwork(online, effectiveType, downlink, rtt) {
    if (online === false) return r(BAD, '오프라인 - 네트워크에 연결되어 있지 않습니다');
    if (!effectiveType) return r(OK, '온라인 (상세 정보는 이 브라우저에서 제공되지 않음)');
    var msg = effectiveType + (isNum(downlink) ? ', 약 ' + downlink + 'Mbps' : '') + (isNum(rtt) ? ', 지연 ' + rtt + 'ms' : '');
    if (effectiveType === 'slow-2g' || effectiveType === '2g') return r(BAD, msg + ' - 매우 느림');
    if (effectiveType === '3g' || (isNum(downlink) && downlink < 1.5)) return r(WARN, msg + ' - 느림');
    return r(OK, msg);
  }

  // 고정 연산량의 소요 시간(ms). 기준값은 대략적인 추정치입니다.
  function gradeCpu(ms) {
    if (!isNum(ms)) return r(NA, '측정 실패');
    var msg = Math.round(ms) + 'ms (낮을수록 빠름, 추정 기준)';
    if (ms > 600) return r(BAD, msg + ' - 처리 속도가 매우 느립니다');
    if (ms > 250) return r(WARN, msg + ' - 다소 느립니다');
    return r(OK, msg);
  }

  function gradeFps(fps) {
    if (!isNum(fps)) return r(NA, '측정 실패 (화면이 백그라운드였을 수 있음)');
    var msg = Math.round(fps) + 'fps';
    if (fps < 30) return r(BAD, msg + ' - 화면 끊김');
    if (fps < 50) return r(WARN, msg + ' - 약간 끊김');
    return r(OK, msg);
  }

  function gradeTouch(hit, total, maxPoints) {
    if (!isNum(hit) || !isNum(total) || total <= 0) return r(NA, '검사 안 함');
    var missed = total - hit;
    var multi = isNum(maxPoints) && maxPoints > 0 ? ', 동시 터치 최대 ' + maxPoints + '점' : '';
    if (missed === 0) return r(OK, '전체 ' + total + '칸 모두 반응' + multi);
    var msg = total + '칸 중 ' + missed + '칸 무반응' + multi;
    return r(missed / total <= 0.05 ? WARN : BAD, msg + ' - 터치 불량 구역 의심');
  }

  function gradeMic(peak) {
    if (!isNum(peak)) return r(NA, '측정 실패');
    if (peak < 0.01) return r(BAD, '소리가 감지되지 않았습니다 (최대 음량 ' + peak.toFixed(3) + ')');
    return r(OK, '소리 감지됨 (최대 음량 ' + peak.toFixed(2) + ')');
  }

  function gradeSensor(eventCount, hasValues) {
    if (!eventCount) return r(BAD, '센서 이벤트가 오지 않았습니다');
    if (!hasValues) return r(WARN, '이벤트는 오지만 값이 비어 있습니다');
    return r(OK, '가속도 센서 이벤트 ' + eventCount + '회 수신');
  }

  function yesNo(yes, okMsg, badMsg) { return yes ? r(OK, okMsg) : r(BAD, badMsg); }

  function summarize(statuses) {
    var c = { ok: 0, warn: 0, bad: 0, na: 0, pending: 0 };
    statuses.forEach(function (s) { if (c.hasOwnProperty(s)) c[s]++; });
    var counted = c.ok + c.warn + c.bad;
    var score = counted ? Math.round(((c.ok + c.warn * 0.5) / counted) * 100) : null;
    var verdict;
    if (c.bad > 0) verdict = '문제가 발견되었습니다';
    else if (c.warn > 0) verdict = '일부 항목에 주의가 필요합니다';
    else if (c.ok > 0) verdict = '검사한 항목에서는 이상이 없습니다';
    else verdict = '아직 검사 전입니다';
    return { counts: c, score: score, verdict: verdict };
  }

  function fmtBytes(n) {
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + u[i];
  }

  var api = {
    OK: OK, WARN: WARN, BAD: BAD, NA: NA, PENDING: PENDING,
    gradeBattery: gradeBattery, gradeStorage: gradeStorage, gradeMemory: gradeMemory,
    gradeNetwork: gradeNetwork, gradeCpu: gradeCpu, gradeFps: gradeFps,
    gradeTouch: gradeTouch, gradeMic: gradeMic, gradeSensor: gradeSensor,
    yesNo: yesNo, summarize: summarize, fmtBytes: fmtBytes
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Diag = api;
})(typeof self !== 'undefined' ? self : this);
