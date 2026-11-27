/* Faixa "Ao vivo" das páginas públicas: aparece só enquanto a sessão está sendo transmitida. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const urlSegura = u => { const t = String(u ?? '').trim(); return /^(https?:\/\/|\/(?![\/\\]))/i.test(t) ? t : ''; };
  const faixa = document.createElement('section');
  faixa.className = 'aovivo';
  faixa.hidden = true;
  faixa.setAttribute('aria-live', 'polite');
  const css = document.createElement('style');
  css.textContent = `.aovivo[hidden]{display:none}.aovivo{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin:0 0 16px;padding:12px 16px;border-radius:14px;
      background:color-mix(in srgb,var(--destaque,#1f8a4c) 18%,var(--cartao,#111a2b));border:1px solid color-mix(in srgb,var(--destaque,#1f8a4c) 45%,transparent)}
    .aovivo .sinal{display:inline-flex;align-items:center;gap:8px;font-weight:800;letter-spacing:.08em;font-size:13px;text-transform:uppercase}
    .aovivo .sinal i{width:10px;height:10px;border-radius:50%;background:#ff4d4d;box-shadow:0 0 10px #ff4d4d;animation:pulsar 1.6s infinite}
    .aovivo .info{flex:1;min-width:200px;font-size:14px}.aovivo .leg{flex-basis:100%;font-size:15px;line-height:1.4;min-height:1.4em;color:var(--texto,#fff)}.aovivo audio{height:36px;max-width:100%}
    @keyframes pulsar{50%{opacity:.35}}@media (prefers-reduced-motion:reduce){.aovivo .sinal i{animation:none}}`;
  document.head.append(css);
  let url = '';
  async function atualizar() {
    const d = await fetch('/api/aovivo').then(r => r.json()).catch(() => null);
    if (d && !d.ativa && d.radio) {  // fora da sessão: a rádio da Câmara com a programação
      faixa.hidden = false;
      if (url !== d.radio + '#prog') {
        url = d.radio + '#prog';
        faixa.innerHTML = `<span class="sinal" style="letter-spacing:.04em">📻 Rádio da Câmara</span><span class="info">${esc(d.casa)} — programação</span><audio controls preload="none" src="${esc(urlSegura(d.radio))}"></audio>`;
      }
      return;
    }
    if (!d || !d.ativa || !(d.publico || d.youtube)) { faixa.hidden = true; return; }
    faixa.hidden = false;
    const info = `<b>${esc(d.nome || d.casa)}</b> · ${esc(d.titulo)}${d.falando ? ` · na tribuna: <b>${esc(d.falando.nome)}</b> (${esc(d.falando.partido)})` : ''}`;
    if (url !== (d.publico || '') + '|' + (d.youtube || '')) {
      url = (d.publico || '') + '|' + (d.youtube || '');
      faixa.innerHTML = `<span class="sinal"><i></i>Ao vivo</span><span class="info"></span>
        ${d.youtube ? `<a class="botao" href="${esc(urlSegura(d.youtube))}" target="_blank" rel="noopener" style="text-decoration:none">▶ Assistir no YouTube</a>` : ''}
        ${d.publico ? `<audio controls preload="none" src="${esc(urlSegura(d.publico))}"></audio>` : ''}
        <div class="leg" aria-live="polite" title="Legenda automática"></div>`;
    }
    faixa.querySelector('.info').innerHTML = info + (d.perguntas && !location.pathname.startsWith('/participe') ? ' · <a href="/participe#ao-vivo">✋ envie sua pergunta</a>' : '');
    if (!legendando) { legendando = true; legendas(); }
  }
  /* legendas automáticas enquanto a sessão está no ar */
  let legendando = false, desde = Date.now() - 20000;
  async function legendas() {
    if (faixa.hidden) { legendando = false; return; }
    const r = await fetch('/api/legendas?desde=' + desde).then(x => x.json()).catch(() => null);
    if (r && r.itens.length) {
      desde = r.itens[r.itens.length - 1].ts;
      const caixa = faixa.querySelector('.leg');
      if (caixa) caixa.textContent = r.itens.slice(-2).map(i => i.texto).join(' ');
    }
    setTimeout(legendas, 3000);
  }
  addEventListener('DOMContentLoaded', () => {
    const main = document.querySelector('main');
    if (main) main.prepend(faixa);
    atualizar();
    setInterval(atualizar, 20000);
  });
})();
