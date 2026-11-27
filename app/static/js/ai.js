// AI 학습 도우미 패널
(function () {
  const panel = document.getElementById('ai-panel');
  const body = document.getElementById('ai-body');
  const input = document.getElementById('ai-input');
  const sendBtn = document.getElementById('ai-send');
  const num = typeof PROBLEM_NUMBER !== 'undefined' ? PROBLEM_NUMBER : null;
  const KEY = `ai:${num}`;
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (e) { history = []; }
  history.forEach(m => add(m.role === 'user' ? 'user' : 'bot', m.role === 'user' ? esc(m.content) : m.html || esc(m.content)));

  function add(cls, html) {
    const d = document.createElement('div');
    d.className = 'msg ' + cls; d.innerHTML = html; body.appendChild(d); body.scrollTop = body.scrollHeight; return d;
  }
  function open() { panel.classList.add('open'); setTimeout(() => input.focus(), 50); }
  document.getElementById('ai-fab').addEventListener('click', () => panel.classList.contains('open') ? panel.classList.remove('open') : open());
  document.getElementById('ai-close').addEventListener('click', () => panel.classList.remove('open'));

  let busy = false;
  async function ask(payload, shownText) {
    if (busy) return;
    busy = true; sendBtn.disabled = true; open();
    add('user', esc(shownText));
    const wait = add('bot', '<span class="muted">생각하는 중…</span>');
    payload.problem = num;
    payload.history = history.slice(-8).map(m => ({role: m.role, content: m.content}));
    const r = await api('/api/ai', payload);
    busy = false; sendBtn.disabled = false;
    if (r.error) { wait.className = 'msg bot err'; wait.textContent = r.error; return; }
    wait.innerHTML = r.html + (r.remaining !== null && r.remaining !== undefined ? `<div class="muted small mt">오늘 남은 질문: ${r.remaining}회</div>` : '');
    history.push({role: 'user', content: shownText}, {role: 'assistant', content: r.answer, html: r.html});
    sessionStorage.setItem(KEY, JSON.stringify(history.slice(-20)));
  }
  function send() {
    const text = input.value.trim(); if (!text) return;
    input.value = '';
    const code = typeof window.getEditorCode === 'function' ? window.getEditorCode() : '';
    ask({kind: 'chat', message: text, code}, text);
  }
  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  document.querySelectorAll('.ai-quick [data-q]').forEach(b => b.addEventListener('click', () => {
    const code = typeof window.getEditorCode === 'function' ? window.getEditorCode() : '';
    ask({kind: 'chat', message: b.dataset.q, code}, b.dataset.q);
  }));

  window.AI = {
    open,
    askQuiz: (quizId, answer) => ask({kind: 'quiz', quiz_id: Number(quizId), quiz_answer: String(answer), message: ''}, '이 퀴즈를 왜 틀렸는지 알려 주세요.'),
    askSubmission: sid => ask({kind: 'submission', submission_id: sid, message: ''}, `제출 #${sid}이 왜 틀렸는지 힌트를 주세요.`),
  };
})();
