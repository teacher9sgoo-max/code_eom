// 문제 풀이 화면: 단계 전환, 퀴즈, 코드 편집기, 실행/제출
(function () {
  const STORAGE_KEY = `code:${PROBLEM_NUMBER}`;
  // ---------- 단계 전환 ----------
  const steps = document.querySelectorAll('.step');
  function show(step) {
    steps.forEach(s => s.classList.toggle('active', s.dataset.step === step));
    document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + step));
    history.replaceState(null, '', '#' + step);
    if (step === 'code' && editor && editor.refresh) setTimeout(() => editor.refresh(), 10);
    window.scrollTo({top: 0, behavior: 'smooth'});
  }
  steps.forEach(s => s.addEventListener('click', () => show(s.dataset.step)));
  document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => show(b.dataset.goto)));

  // ---------- 편집기 ----------
  const ta = document.getElementById('code-fallback');
  const saved = localStorage.getItem(STORAGE_KEY);
  ta.value = saved !== null ? saved : (INITIAL_CODE || '');
  let editor = null;
  if (window.CodeMirror) {
    editor = CodeMirror.fromTextArea(ta, {
      mode: 'python', theme: 'material-darker', lineNumbers: true, indentUnit: 4, tabSize: 4,
      matchBrackets: true, indentWithTabs: false,
      extraKeys: {
        Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('    ', 'end'),
        'Shift-Tab': cm => cm.indentSelection('subtract'),
        'Ctrl-Enter': () => run(), 'Cmd-Enter': () => run(),
        'Ctrl-S': () => submit(), 'Cmd-S': () => submit(),
      },
    });
    editor.on('change', () => localStorage.setItem(STORAGE_KEY, editor.getValue()));
  } else {
    ta.addEventListener('keydown', e => {
      if (e.key === 'Tab') { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText('    ', s, ta.selectionEnd, 'end'); }
    });
    ta.addEventListener('input', () => localStorage.setItem(STORAGE_KEY, ta.value));
  }
  const getCode = () => editor ? editor.getValue() : ta.value;
  const setCode = c => { editor ? editor.setValue(c) : (ta.value = c); localStorage.setItem(STORAGE_KEY, c); };
  window.getEditorCode = getCode;

  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's' && document.getElementById('pane-code').classList.contains('active')) { e.preventDefault(); submit(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !editor) { e.preventDefault(); run(); }
  });
  document.getElementById('btn-reset').addEventListener('click', () => {
    if (confirm('작성한 코드를 모두 지울까요?')) setCode('');
  });
  document.querySelectorAll('.load-code').forEach(b => b.addEventListener('click', async () => {
    const r = await api(`/api/submission/${b.dataset.sid}?code=1`);
    const code = r.code;
    if (code !== undefined && confirm('현재 코드를 선택한 제출 코드로 바꿀까요?')) setCode(code);
  }));
  document.querySelectorAll('.copy').forEach(b => b.addEventListener('click', () => {
    navigator.clipboard?.writeText(b.dataset.copy); b.textContent = '복사됨'; setTimeout(() => b.textContent = '복사', 1200);
  }));

  // ---------- 실행 ----------
  const runBtn = document.getElementById('btn-run');
  const runOut = document.getElementById('run-output');
  async function run() {
    if (!runBtn || runBtn.disabled) return;
    runBtn.disabled = true; runOut.textContent = '실행 중…'; runOut.style.color = 'var(--text)';
    const r = await api(`/api/run/${PROBLEM_NUMBER}`, {code: getCode(), input: document.getElementById('run-input').value});
    runBtn.disabled = false;
    if (r.error && !r.status) { runOut.textContent = r.error; return; }
    let text = r.output || '';
    if (r.status !== 'OK') {
      text += (text ? '\n' : '') + `[${r.label}]` + (r.error ? '\n' + r.error : '');
      runOut.style.color = '#b91c1c';
    } else if (!text) text = '(출력 없음)';
    runOut.textContent = text;
  }
  if (runBtn) runBtn.addEventListener('click', run);

  // ---------- 제출 ----------
  const subBtn = document.getElementById('btn-submit');
  const resultEl = document.getElementById('result');
  async function submit() {
    if (subBtn.disabled) return;
    const code = getCode();
    if (!code.trim()) { alertBox('코드를 입력하세요.'); return; }
    subBtn.disabled = true;
    const r = await api(`/api/submit/${PROBLEM_NUMBER}`, {code});
    if (r.error) { subBtn.disabled = false; alertBox(r.error); return; }
    renderResult(resultEl, {done: false, label: '채점 중'});
    resultEl.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    pollSubmission(r.id, v => {
      renderResult(resultEl, v, {ai: AI_ENABLED});
      if (v.done) {
        subBtn.disabled = false;
        if (v.status === 'AC') document.querySelector('.step[data-step="code"]').classList.add('done');
      }
    });
  }
  subBtn.addEventListener('click', submit);
  function alertBox(msg) { resultEl.innerHTML = `<div class="flash error" style="margin:0">${esc(msg)}</div>`; }

  if (LAST_PENDING) { renderResult(resultEl, {done: false, label: '채점 중'}); pollSubmission(LAST_PENDING, v => renderResult(resultEl, v, {ai: AI_ENABLED})); }
  else if (LAST_VIEW) renderResult(resultEl, LAST_VIEW, {ai: AI_ENABLED});

  // ---------- 퀴즈 ----------
  document.querySelectorAll('.quiz').forEach(box => {
    const qid = box.dataset.quiz, kind = box.dataset.kind;
    const fb = box.querySelector('.feedback-area');
    box.querySelector('.quiz-submit').addEventListener('click', async () => {
      let answer = '';
      if (kind === 'choice') {
        const sel = box.querySelector('input[type=radio]:checked');
        if (!sel) { fb.innerHTML = '<div class="feedback no">보기를 선택하세요.</div>'; return; }
        answer = sel.value;
      } else {
        answer = box.querySelector('.short-answer').value;
        if (!answer.trim()) { fb.innerHTML = '<div class="feedback no">정답을 입력하세요.</div>'; return; }
      }
      const r = await api(`/api/quiz/${qid}`, {answer});
      if (r.error) { fb.innerHTML = `<div class="feedback no">${esc(r.error)}</div>`; return; }
      box.querySelectorAll('.opt').forEach(o => o.classList.remove('right', 'wrong'));
      let html = '';
      if (r.correct) {
        box.classList.add('correct');
        html = `<div class="feedback ok"><strong>정답입니다!</strong>${r.explanation ? '<div class="md">' + r.explanation + '</div>' : ''}</div>`;
        if (kind === 'choice') box.querySelector(`.opt[data-idx="${answer}"]`)?.classList.add('right');
      } else {
        if (kind === 'choice') box.querySelector(`.opt[data-idx="${answer}"]`)?.classList.add('wrong');
        html = `<div class="feedback no"><strong>아쉬워요, 다시 생각해 보세요.</strong>`;
        if (r.explanation) {
          if (r.answer !== undefined) box.querySelector(`.opt[data-idx="${r.answer}"]`)?.classList.add('right');
          html += `${r.answer_text ? '<div>정답: <code>' + esc(r.answer_text) + '</code></div>' : ''}<div class="md">${r.explanation}</div>`;
        } else html += `<div class="small">한 번 더 틀리면 정답과 해설을 보여줍니다.</div>`;
        html += `</div>`;
        if (AI_ENABLED) html += `<div class="btn-row mt"><button class="btn sm primary ask-ai">AI 도우미에게 왜 틀렸는지 묻기</button></div>`;
      }
      fb.innerHTML = html;
      fb.querySelector('.ask-ai')?.addEventListener('click', () => AI.askQuiz(qid, answer));
      const done = document.querySelectorAll('.quiz.correct').length, total = document.querySelectorAll('.quiz').length;
      const qc = document.getElementById('quiz-count'); if (qc) qc.textContent = `(${done}/${total})`;
      if (r.all_done) document.getElementById('step-quiz').classList.add('done');
    });
  });

  // 처음 들어오면: 해결했거나 퀴즈를 마쳤으면 코딩 단계, 아니면 개념부터
  const hash = location.hash.replace('#', '');
  show(['concept', 'quiz', 'code'].includes(hash) ? hash : (SOLVED || (QUIZ_DONE && HAS_QUIZ)) ? 'code' : 'concept');
})();
