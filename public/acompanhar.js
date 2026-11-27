/* Acompanhar: "Acompanhar (RSS)", "Adicionar à agenda" e, quando a Câmara configurou o e-mail, aviso por e-mail.
   Uso declarativo: <div data-acompanhar data-feed="/feeds/projetos.atom" data-agenda="/agenda.ics" data-projeto="ID"></div>
   data-feed="vereador" usa o parlamentar do endereço (/parlamentares/<id>-nome).
   Para conteúdo desenhado depois: Acompanhar.montar(elemento). */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let emailDisp = null;
  const temEmail = () => (emailDisp ??= fetch('/api/acompanhar').then(r => r.json()).then(j => !!j.email).catch(() => false));
  const webcal = caminho => 'webcal://' + location.host + caminho;   // o calendário assina e atualiza sozinho

  function autodescoberta(feed, titulo) {
    if (!feed || document.querySelector(`link[rel=alternate][href="${CSS.escape(feed)}"]`)) return;
    const l = document.createElement('link');
    l.rel = 'alternate'; l.type = 'application/atom+xml'; l.href = feed; l.title = titulo || 'Acompanhar';
    document.head.append(l);
  }

  async function montar(el) {
    let feed = el.dataset.feed || '';
    if (feed === 'vereador') {
      const pid = decodeURIComponent(location.pathname.split('/').pop() || '').split('-')[0];
      feed = /^[A-Za-z0-9]+$/.test(pid) ? `/feeds/vereador/${pid}.atom` : '';
    }
    const agenda = el.dataset.agenda || '', projeto = el.dataset.projeto || '';
    autodescoberta(feed, el.dataset.titulo);
    el.classList.add('acomp');
    el.innerHTML = `<div class="grupo">
      ${feed ? `<a class="botao pq" href="${esc(feed)}" type="application/atom+xml" title="Cole este endereço no seu leitor de notícias (RSS/Atom)">📡 Acompanhar (RSS)</a>` : ''}
      ${agenda ? `<a class="botao pq" href="${esc(webcal(agenda))}" title="Assina a agenda no calendário do celular ou do computador">📅 Adicionar à agenda</a>
        <a class="botao pq fantasma" href="${esc(agenda)}" download="agenda-camara.ics">baixar .ics</a>` : ''}</div>`;
    if (!projeto || !(await temEmail())) return;
    const f = document.createElement('form');
    f.className = 'acomp-email';
    f.innerHTML = `<input class="campo" type="email" name="email" required maxlength="254" autocomplete="email" placeholder="seu@email.com.br" aria-label="E-mail para receber avisos deste projeto">
      <input class="isca" name="site" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px">
      <button class="botao pq principal">✉️ Avisar por e-mail</button>
      <small class="suave">Enviamos um link de confirmação; só depois dele chegam os avisos. O e-mail serve só para isso, não é publicado e você cancela quando quiser (link em cada mensagem).</small>`;
    f.onsubmit = async ev => {
      ev.preventDefault();
      const corpo = { ...Object.fromEntries(new FormData(f)), projeto };
      const r = await fetch('/api/acompanhar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return alert(j.erro || 'Não foi possível inscrever.');
      f.reset();
      alert(j.mensagem || 'Confira seu e-mail para confirmar.');
    };
    el.append(f);
  }

  const css = document.createElement('style');
  css.textContent = `.acomp{display:flex;flex-direction:column;gap:8px}.acomp-email{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
    .acomp-email input[type=email]{flex:1;min-width:200px}.acomp-email small{flex-basis:100%}
    .citacao{text-decoration:underline dotted;text-underline-offset:3px}a.citacao{color:inherit;text-decoration-color:var(--destaque,#1f8a4c)}`;
  document.head.append(css);
  const iniciar = () => document.querySelectorAll('[data-acompanhar]').forEach(montar);
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', iniciar) : iniciar();
  window.Acompanhar = { montar };
})();
