// 공통 유틸
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || '';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function api(url, data, method) {
  const opt = {method: method || (data === undefined ? 'GET' : 'POST'), headers: {'X-CSRFToken': CSRF}};
  if (data !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(data); }
  let res;
  try { res = await fetch(url, opt); } catch (e) { return {error: '서버에 연결할 수 없습니다.'}; }
  let body = {};
  try { body = await res.json(); } catch (e) { body = {error: `요청 실패 (HTTP ${res.status})`}; }
  if (!res.ok && !body.error) body.error = `요청 실패 (HTTP ${res.status})`;
  return body;
}

const STATUS_CLASS = s => 'st-' + s;

function renderResult(el, v, opts = {}) {
  if (!v) return;
  if (!v.done) {
    el.innerHTML = `<div class="result-head"><span class="badge st-RUNNING">${esc(v.label)}</span><span class="muted">잠시만 기다려 주세요…</span></div>`;
    return;
  }
  let html = `<div class="result-head"><span class="big badge ${STATUS_CLASS(v.status)}" style="font-size:1rem;padding:4px 12px">${esc(v.label)}</span>
    <span><strong>${v.passed}</strong> / ${v.total} 통과</span><span class="muted small">최대 ${Number(v.max_time||0).toFixed(3)}초</span>
    <a class="small" href="${v.url}" style="margin-left:auto">제출 #${v.id}</a></div>`;
  if (v.status === 'AC') html += `<div class="feedback ok mb">모든 테스트 케이스를 통과했습니다. 다음 문제에 도전해 보세요!</div>`;
  if (v.compile_error) html += `<div class="section-label">문법 오류</div><pre class="err" style="background:#fff7f7;color:#991b1b;border:1px solid #f5c2c2">${esc(v.compile_error)}</pre>`;
  if (v.cases && v.cases.length) {
    html += '<div class="case-chips">' + v.cases.map(c => `<span class="chip ${STATUS_CLASS(c.status)}" title="${c.no}번: ${esc(c.label)}">${c.no}</span>`).join('') + '</div>';
    let openedOne = false;
    html += '<div class="cases">' + v.cases.map(c => {
      const fail = c.status !== 'AC';
      const open = fail && !openedOne; if (open) openedOne = true;
      let body = '';
      if (c.visible) {
        body += `<div class="cols"><div><div class="small muted">입력</div><pre>${esc(c.input) || '(없음)'}</pre></div>
          <div><div class="small muted">기대 출력</div><pre>${esc(c.expected)}</pre></div>
          <div><div class="small muted">내 출력</div><pre>${esc(c.output) || '(출력 없음)'}</pre></div></div>`;
      } else {
        body += `<p class="muted small" style="margin:4px 0">비공개 채점 케이스입니다. 예제 외의 다양한 입력을 스스로 시험해 보세요.</p>`;
      }
      if (c.error) body += `<div class="small muted mt">오류 메시지</div><pre class="err">${esc(c.error)}</pre>`;
      return `<details class="case" ${open ? 'open' : ''}><summary><span class="badge ${STATUS_CLASS(c.status)}">${esc(c.label)}</span>
        <span>${c.no}번 케이스${c.sample ? ' (예제)' : ''}</span><span class="muted small" style="margin-left:auto">${Number(c.time||0).toFixed(3)}초</span></summary>
        <div class="body">${body}</div></details>`;
    }).join('') + '</div>';
  }
  if (v.status !== 'AC' && opts.ai) {
    html += `<div class="btn-row mt"><button class="btn primary" onclick="window.AI && AI.askSubmission(${v.id})">AI 도우미에게 틀린 이유 물어보기</button></div>`;
  }
  el.innerHTML = html;
}

function pollSubmission(id, cb, tries = 0) {
  setTimeout(async () => {
    const v = await api(`/api/submission/${id}`);
    if (v.error) { cb({done: true, status: 'SE', label: '채점 오류', passed: 0, total: 0, cases: [], compile_error: v.error, id, url: '#'}); return; }
    cb(v);
    if (!v.done && tries < 120) pollSubmission(id, cb, tries + 1);
  }, tries < 5 ? 600 : 1500);
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-confirm]');
  if (b && !confirm(b.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); }
}, true);
