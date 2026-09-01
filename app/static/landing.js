(() => {
  const clock = document.getElementById('clock');
  const year = document.getElementById('year');
  const topCta = document.getElementById('topCta');
  const heroCta = document.getElementById('heroCta');
  const openCta = document.getElementById('openCta');
  const loginCta = document.getElementById('loginCta');

  function updateClock(){
    const now = new Date();
    if (clock) clock.textContent = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  }
  updateClock();
  setInterval(updateClock, 1000);
  if (year) year.textContent = new Date().getFullYear();

  async function updateSessionCtas(){
    try {
      const setup = await fetch('/api/auth/setup-status', {credentials:'same-origin'}).then(r => r.ok ? r.json() : null);
      if (setup?.needs_setup) {
        [topCta, heroCta, openCta, loginCta].filter(Boolean).forEach(a => a.href='/login');
        if (topCta) topCta.textContent='Create account';
        if (heroCta) heroCta.textContent='Create first account';
        if (openCta) openCta.textContent='Set up Tasia Streamer';
        if (loginCta) loginCta.textContent='First-time setup';
        return;
      }
      const me = await fetch('/api/me', {credentials:'same-origin'});
      if (me.ok) {
        [topCta, heroCta, openCta].filter(Boolean).forEach(a => a.href='/app');
        if (topCta) topCta.textContent='Open workstation';
        if (heroCta) heroCta.textContent='Open workstation';
        if (openCta) openCta.textContent='Open workstation';
        if (loginCta) { loginCta.href='/app'; loginCta.textContent='Back to DJ desk'; }
      }
    } catch (_) {}
  }
  updateSessionCtas();

  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', event => {
      const id = link.getAttribute('href');
      if (!id || id === '#') return;
      const target = document.querySelector(id);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({behavior:'smooth', block:'start'});
    });
  });
})();
