/* Rodapé público: contatos da câmara (esquerda) e "Desenvolvido por Aggilli Intelligence" (direita).
   Uso: Rodape.mostrar(rodape, casa) — só aparecem os campos preenchidos em Configurações. */
const Rodape = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = d => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICONES = {
    instagram: ic('<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor"/>'),
    facebook: ic('<path d="M15 3h-2.5A3.5 3.5 0 0 0 9 6.5V10H6.5v3.5H9V21h3.5v-7.5H15l.5-3.5h-3V7a1 1 0 0 1 1-1H15z"/>'),
    youtube: ic('<rect x="2.5" y="5.5" width="19" height="13" rx="4"/><path d="m10 9.5 5 2.5-5 2.5z" fill="currentColor"/>'),
    linkedin: ic('<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 10v7M8 7v.01M12 17v-4a2 2 0 0 1 4 0v4M12 10v7"/>'),
    whatsapp: ic('<path d="M4 20l1.3-3.8A8 8 0 1 1 8 19z"/><path d="M9.5 9.5c0 3 2 5 5 5l1-1.5-2-1-1 1c-1 0-2.5-1.5-2.5-2.5l1-1-1-2z"/>'),
    email: ic('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>'),
    telefone: ic('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>'),
    site: ic('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
    endereco: ic('<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
  };
  const ROTULO = { instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube', linkedin: 'LinkedIn', whatsapp: 'WhatsApp' };
  const arroba = url => { const m = url.match(/instagram\.com\/([^/?#]+)/i); return m ? '@' + m[1] : ROTULO.instagram; };
  const semProtocolo = url => url.replace(/^https?:\/\//, '').replace(/\/$/, '');

  function contatos(c) {
    const linhas = [];
    if (c.endereco) linhas.push(`<li>${ICONES.endereco}<span>${esc(c.endereco)}</span></li>`);
    if (c.telefone) linhas.push(`<li>${ICONES.telefone}<a href="tel:${esc(c.telefone.replace(/[^\d+]/g, ''))}">${esc(c.telefone)}</a></li>`);
    if (c.whatsapp) linhas.push(`<li>${ICONES.whatsapp}<a href="https://wa.me/${esc(c.whatsapp.replace(/\D/g, '').replace(/^(?!55)/, '55'))}" target="_blank" rel="noopener noreferrer">${esc(c.whatsapp)}</a></li>`);
    if (c.email) linhas.push(`<li>${ICONES.email}<a href="mailto:${esc(c.email)}">${esc(c.email)}</a></li>`);
    if (c.site) linhas.push(`<li>${ICONES.site}<a href="${esc(c.site)}" target="_blank" rel="noopener noreferrer">${esc(semProtocolo(c.site))}</a></li>`);
    const redes = ['instagram', 'facebook', 'youtube', 'linkedin'].filter(k => c[k]).map(k =>
      `<a class="rede" href="${esc(c[k])}" target="_blank" rel="noopener noreferrer" aria-label="${ROTULO[k]}" title="${k === 'instagram' ? esc(arroba(c[k])) : ROTULO[k]}">${ICONES[k]}${k === 'instagram' ? `<span>${esc(arroba(c[k]))}</span>` : ''}</a>`).join('');
    return (linhas.length ? `<ul>${linhas.join('')}</ul>` : '') + (redes ? `<div class="redes">${redes}</div>` : '');
  }

  function mostrar(r, casa) {
    if (!r) return;
    document.getElementById('rodapePublico')?.remove();
    if (!document.getElementById('estiloRodape')) {
      const st = document.createElement('style'); st.id = 'estiloRodape';
      st.textContent = `
      .rodape-pub { margin-top: 32px; background: var(--fundo-fundo); border-top: 4px solid var(--destaque-suave); color: var(--suave); font-size: 14px }
      .rodape-pub .grade { max-width: 1120px; margin: 0 auto; padding: 28px 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 28px }
      @media (max-width: 760px) { .rodape-pub .grade { grid-template-columns: 1fr } }
      .rodape-pub .col { display: flex; flex-direction: column; gap: 12px }
      .rodape-pub .col.dir { align-items: flex-end; text-align: right }
      @media (max-width: 760px) { .rodape-pub .col.dir { align-items: flex-start; text-align: left } }
      .rodape-pub .marca-r { display: flex; align-items: center; gap: 12px; color: var(--texto); font-weight: 700; font-size: 16px }
      .rodape-pub .marca-r img { width: 48px; height: 48px; object-fit: contain; background: #fff; border-radius: 10px; padding: 4px }
      .rodape-pub .marca-r small { display: block; font-weight: 400; color: var(--suave); font-size: 12px }
      .rodape-pub ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px }
      .rodape-pub li { display: flex; gap: 8px; align-items: flex-start }
      .rodape-pub .col.dir li { flex-direction: row-reverse }
      @media (max-width: 760px) { .rodape-pub .col.dir li { flex-direction: row } }
      .rodape-pub li svg { flex: none; margin-top: 2px; color: var(--destaque) }
      .rodape-pub a { color: var(--texto); text-decoration: none } .rodape-pub a:hover { text-decoration: underline }
      .rodape-pub .redes { display: flex; gap: 8px; flex-wrap: wrap }
      .rodape-pub .rede { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 10px; background: var(--cartao2) }
      .rodape-pub .rede:hover { background: var(--cartao3); text-decoration: none }
      .rodape-pub .fim { text-align: center; font-size: 12px; padding: 0 16px 18px }`;
      document.head.append(st);
    }
    const c = r.camara || {}, a = r.aggilli || {};
    const logoCasa = casa?.logoV ? `/imagem/logo?v=${casa.logoV}` : '/marca/logo.svg';
    const f = document.createElement('footer');
    f.id = 'rodapePublico'; f.className = 'rodape-pub';
    f.innerHTML = `<div class="grade">
        <div class="col"><div class="marca-r"><img src="${logoCasa}" alt="Brasão">${esc(casa?.nome || 'Câmara Municipal')}</div>${contatos(c)}</div>
        <div class="col dir"><a class="marca-r" href="${esc(a.site || 'https://www.aggillintelligence.com.br')}" target="_blank" rel="noopener noreferrer" style="color:var(--texto)">
          <span><small>Desenvolvido por</small>Aggilli Intelligence</span><img src="/marca/logo.svg" alt="Aggilli Intelligence"></a>${contatos(a)}</div>
      </div><div class="fim">Parlatório Legis · software livre para casas legislativas</div>`;
    document.body.append(f);
  }
  return { mostrar };
})();
