/* Faixa "Ao vivo" das páginas públicas: aparece só enquanto a sessão está sendo transmitida. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const faixa = document.createElement('section');
  faixa.className = 'aovivo';
  faixa.hidden = true;
  faixa.setAttribute('aria-live', 'polite');
  const css = document.createElement('style');
  css.textContent = `.aovivo{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin:0 0 16px;padding:12px 16px;border-radius:14px;
      background:color-mix(in srgb,var(--destaque,#d66060) 18%,var(--cartao,#111a2b));border:1px solid color-mix(in srgb,var(--destaque,#d66060) 45%,transparent)}
    .aovivo .sinal{display:inline-flex;align-items:center;gap:8px;font-weight:800;letter-spacing:.08em;font-size:13px;text-transform:uppercase}
    .aovivo .sinal i{width:10px;height:10px;border-radius:50%;background:#ff4d4d;box-shadow:0 0 10px #ff4d4d;animation:pulsar 1.6s infinite}
    .aovivo .info{flex:1;min-width:200px;font-size:14px}.aovivo audio{height:36px;max-width:100%}
    @keyframes pulsar{50%{opacity:.35}}@media (prefers-reduced-motion:reduce){.aovivo .sinal i{animation:none}}`;
  document.head.append(css);
  let url = '';
  async function atualizar() {
    const d = await fetch('/api/aovivo').then(r => r.json()).catch(() => null);
    if (!d || !d.ativa || !d.publico) { faixa.hidden = true; return; }
    faixa.hidden = false;
    const info = `<b>${esc(d.nome || d.casa)}</b> · ${esc(d.titulo)}${d.falando ? ` · na tribuna: <b>${esc(d.falando.nome)}</b> (${esc(d.falando.partido)})` : ''}`;
    if (url !== d.publico) {
      url = d.publico;
      faixa.innerHTML = `<span class="sinal"><i></i>Ao vivo</span><span class="info"></span><audio controls preload="none" src="${esc(url)}"></audio>`;
    }
    faixa.querySelector('.info').innerHTML = info;
  }
  addEventListener('DOMContentLoaded', () => {
    const main = document.querySelector('main');
    if (main) main.prepend(faixa);
    atualizar();
    setInterval(atualizar, 20000);
  });
})();
