// static/theme.js — alternância e persistência do tema
(function(){
  const root = document.documentElement;
  const saved = localStorage.getItem('theme') || 'light';
  root.setAttribute('data-theme', saved);

  function labelFor(t){ return t==='dark' ? 'Light' : 'Black'; }
  function iconFor(t){ return t==='dark' ? '☀️' : '🌙'; }

  function updateBtn(btn){
    const t = root.getAttribute('data-theme');
    const icon = btn.querySelector('.icon');
    const label = btn.querySelector('.label');
    if(icon) icon.textContent = iconFor(t);
    if(label) label.textContent = labelFor(t);
  }

  window.applyThemeToggle = function(id){
    const btn = document.getElementById(id);
    if(!btn) return;
    updateBtn(btn);
    btn.addEventListener('click', ()=>{
      const next = root.getAttribute('data-theme')==='light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
      updateBtn(btn);
    });
  };
})();
