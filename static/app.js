// static/app.js
(function (win) {
  function pad(n){ return String(n).padStart(2,'0'); }
  function toHHMMSS(s){
    const h = Math.floor(s/3600);
    const m = Math.floor((s%3600)/60);
    const sec = Math.floor(s%60);
    return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  }
  function toHours2(s){ return Math.round((s/3600)*100)/100; }

  function attach(cfg){
    const display = document.querySelector(cfg.display);
    const startBtn = document.querySelector(cfg.startBtn);
    const pauseBtn = document.querySelector(cfg.pauseBtn);
    const stopBtn  = document.querySelector(cfg.stopBtn);
    if(!display || !startBtn || !pauseBtn || !stopBtn){
      console.warn('Stopwatch: missing elements', {display, startBtn, pauseBtn, stopBtn});
      return;
    }

    let running = false, seconds = 0, tickId = null;

    function tick(){
      if(!running) return;
      seconds += 1;
      display.textContent = toHHMMSS(seconds);
      tickId = setTimeout(tick, 1000);
    }

    startBtn.addEventListener('click', function(ev){
      ev.preventDefault(); // guard against accidental form submit
      if(!running){ running = true; tick(); }
    });

    pauseBtn.addEventListener('click', function(ev){
      ev.preventDefault();
      running = false;
      if(tickId) { clearTimeout(tickId); tickId = null; }
    });

    stopBtn.addEventListener('click', function(ev){
      ev.preventDefault();
      const hoursInput = document.querySelector(stopBtn.dataset.targetHours || '#hours');
      if(hoursInput){
        const h = toHours2(seconds);
        if(h >= 0.01) hoursInput.value = h.toFixed(2);
      }
      running = false; seconds = 0; display.textContent = '00:00:00';
      if(tickId) { clearTimeout(tickId); tickId = null; }
    });
  }

// -------------------- Stopwatch --------------------
(function () {
  const el = (sel) => document.querySelector(sel);

  const startBtn  = el('#sw-start');
  const pauseBtn  = el('#sw-pause');
  const stopBtn   = el('#sw-stop');
  const helpBtn   = el('#sw-help');
  const display   = el('#sw-display');
  const swForm    = el('#sw-form');

  if (!startBtn || !pauseBtn || !stopBtn || !display || !swForm) return;

  // Read threshold from server (minutes -> seconds). Fallback 5 min.
  const STOP_MIN_SECONDS = (function(){
    const elCard = document.getElementById('stopwatch-card');
    // If you want to hardcode from Jinja, replace next line with: return {{ stop_min|default(300) }};
    return {{ stop_min|default(300) }};
  })();

  let running = false;
  let secs = 0;
  let tickHandle = null;

  const fmt = (s) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => (n < 10 ? '0' + n : '' + n);
    return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  };

  const render = () => display.textContent = fmt(secs);

  const tick = () => {
    secs += 1;
    render();
  };

  const start = () => {
    if (running) return;
    running = true;
    // fire every 1s; keep a single interval
    tickHandle = setInterval(tick, 1000);
  };

  const pause = () => {
    if (!running) return;
    running = false;
    if (tickHandle) clearInterval(tickHandle);
    tickHandle = null;
  };

  const reset = () => {
    pause();
    secs = 0;
    render();
  };

  const askDescription = async () => {
    // Simple prompt loop (keeps dependencies at zero).
    // Replace with a custom modal if you want fancier UI.
    while (true) {
      const txt = window.prompt("Describe the work performed for this timed session:", "");
      if (txt === null) {
        const discard = window.confirm("Discard this stopwatch time without logging?");
        if (discard) return null; // user cancelled
        continue; // ask again
      }
      const trimmed = (txt || "").trim();
      if (trimmed) return trimmed;
      window.alert("Please enter a brief description of the work performed.");
    }
  };

  const getFieldVal = (name, fallback="") => {
    const f = document.querySelector(`[name="${name}"]`);
    // For selects/inputs, .value works the same
    return (f && (f.value || f.textContent || "")).trim() || fallback;
  };

  const toHours2 = (s) => Math.round((s / 3600) * 100) / 100;

  startBtn.addEventListener('click', start);
  pauseBtn.addEventListener('click', pause);

  stopBtn.addEventListener('click', async () => {
    const elapsed = secs;
    const wasRunning = running;
    reset();

    if (elapsed < STOP_MIN_SECONDS) {
      window.alert(`Session under ${Math.floor(STOP_MIN_SECONDS/60)} minutes — not logged.`);
      return;
    }

    const desc = await askDescription();
    if (desc === null) return; // discarded by user

    // Copy current selections from the visible entry form (if present)
    const client = getFieldVal('client', '(Unspecified)');
    const matter = getFieldVal('matter', '(Unspecified)');
    const date   = getFieldVal('date_of_work', (new Date()).toISOString().slice(0,10));
    const hours  = toHours2(elapsed);

    // Fill and submit the invisible stopwatch form
    el('#sw-client').value  = client;
    el('#sw-matter').value  = matter;
    el('#sw-date').value    = date;
    el('#sw-hours').value   = hours.toFixed(2);
    el('#sw-desc').value    = desc;
    el('#sw-elapsed').value = String(elapsed);

    swForm.submit();
  });

  helpBtn?.addEventListener('click', () => {
    const mins = Math.floor(STOP_MIN_SECONDS / 60);
    alert(
      [
        "Stopwatch – How it works",
        "",
        "• Start: begins counting time.",
        "• Pause: temporarily halts the clock (you can Start again).",
        "• Stop: ends the session.",
        "",
        `When you press Stop, if the elapsed time is at least ${mins} minutes, the app asks you to`,
        "describe the work performed and automatically adds an entry.",
        "If it’s under the threshold, nothing is added."
      ].join("\n")
    );
  });

  // initial render
  render();
})();

// Trim + password show/hide for login
document.getElementById('login-form')?.addEventListener('submit', (e) => {
  const u = document.querySelector('#login-form [name="username"]');
  const p = document.getElementById('password');
  if (u) u.value = (u.value || '').trim();
  if (p) p.value = (p.value || '').trim();
});
(function () {
  const pw = document.getElementById('password');
  const btn = document.getElementById('toggle-pw');
  if (!pw || !btn) return;
  btn.addEventListener('click', () => {
    const showing = pw.type === 'text';
    pw.type = showing ? 'password' : 'text';
    btn.textContent = showing ? 'Show' : 'Hide';
    btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    pw.focus();
  });
})();
// Select all checkboxes
(function(){
  const all = document.getElementById('check-all');
  if(!all) return;
  const rows = () => Array.from(document.querySelectorAll('.row-check'));
  all.addEventListener('change', () => rows().forEach(cb => cb.checked = all.checked));
})();
// Inline edit toggling
(function(){
  function showRow(id, show){
    const r = document.getElementById('edit-'+id);
    if(!r) return;
    r.style.display = show ? '' : 'none';
  }
  document.querySelectorAll('.js-edit').forEach(btn=>{
    btn.addEventListener('click', (e)=>{
      e.preventDefault();
      showRow(btn.dataset.id, true);
    });
  });
  document.querySelectorAll('.js-cancel').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      showRow(btn.dataset.id, false);
    });
  });
  // Confirm save without extra page scroll
  window.saveRow = function(form){
    // You can add extra client-side validation here if needed
    return true; // let the form submit normally
  }
})();


