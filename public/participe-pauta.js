/* /participe: opinião do cidadão em cada item da pauta da próxima sessão (a favor / contra / neutro) e inscrição
   na Tribuna Livre. Tudo passa pela moderação da presidência antes de aparecer. Desenha em #pauta-cidada e #tribuna-livre. */
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const enviar = async (url, corpo) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.erro || 'Não foi possível enviar.');
    return j;
  };
  const ICONE = { favor: '👍', contra: '👎', neutro: '✋' };

  async function pauta() {
    const el = $('pautaCidadaLista'); if (!el) return;
    const P = await fetch('/api/pauta-cidada').then(r => r.json()).catch(() => null);
    if (!P) { el.innerHTML = '<p class="vazio">Não foi possível carregar a pauta.</p>'; return; }
    $('pautaCidadaTitulo').textContent = `${P.titulo} · ${P.casa}`;
    el.innerHTML = P.itens.length ? P.itens.map(m => {
      const total = Object.values(m.placar).reduce((a, b) => a + b, 0);
      return `<div class="enq" id="pc-${esc(m.id)}">
        <b>${esc(m.numero)}</b> <span class="suave">· ${esc(m.autor || '')}</span><div style="margin-top:2px">${esc(m.ementa)}</div>
        <div class="pc-placar" aria-label="Posicionamentos publicados">${Object.entries(P.posicoes).map(([k, n]) => `<span class="chip">${ICONE[k]} ${esc(n)}: <b>${m.placar[k]}</b></span>`).join(' ')}
          <small class="suave">${total ? 'opiniões publicadas após moderação' : 'nenhuma opinião publicada ainda'}</small></div>
        ${m.comentarios.map(c => `<div class="contrib"><span class="chip">${ICONE[c.posicao]} ${esc(P.posicoes[c.posicao])}</span> <b>${esc(c.nome)}</b>${c.bairro ? ' · ' + esc(c.bairro) : ''}
          <div style="white-space:pre-wrap;margin-top:4px">${esc(c.texto)}</div></div>`).join('')}
        <details><summary class="botao pq" style="display:inline-flex;cursor:pointer;margin-top:8px">Dar minha opinião</summary>
          <form class="form-contrib" data-item="${esc(m.id)}">
            <div class="grupo" role="radiogroup" aria-label="Sua posição">${Object.entries(P.posicoes).map(([k, n]) => `<label class="botao pq fantasma"><input type="radio" name="posicao" value="${k}" required> ${ICONE[k]} ${esc(n)}</label>`).join('')}</div>
            <textarea class="campo" name="texto" rows="3" required minlength="5" maxlength="1500" placeholder="Seu comentário sobre este item" aria-label="Seu comentário sobre este item"></textarea>
            <div class="linha2"><input class="campo" name="nome" required minlength="3" maxlength="80" placeholder="Seu nome" aria-label="Seu nome" autocomplete="name">
              <input class="campo" name="email" type="email" required maxlength="254" placeholder="E-mail (obrigatório, não é publicado)" aria-label="E-mail (obrigatório, não é publicado)" autocomplete="email"></div>
            <input class="campo" name="bairro" maxlength="60" placeholder="Bairro ou povoado (opcional)" aria-label="Bairro ou povoado (opcional)">
            <input class="isca" name="site" tabindex="-1" autocomplete="off" aria-hidden="true">
            <button class="botao principal">Enviar opinião</button>
            <small class="suave">A presidência lê antes de publicar. Seu e-mail fica só com a Câmara, para contato sobre este comentário.</small>
          </form></details></div>`;
    }).join('') : '<p class="vazio">Nenhum item da pauta recebendo opiniões agora.</p>';
    el.querySelectorAll('form[data-item]').forEach(f => f.onsubmit = async ev => {
      ev.preventDefault();
      try { await enviar(`/api/pauta/${encodeURIComponent(f.dataset.item)}/opinar`, Object.fromEntries(new FormData(f))); }
      catch (e) { return alert(e.message); }
      f.reset(); f.closest('details').open = false;
      alert('Obrigado! Sua opinião foi recebida e aparece aqui depois da aprovação da presidência.');
    });
  }

  async function tribuna() {
    const el = $('tribunaLista'); if (!el) return;
    const T = await fetch('/api/tribuna').then(r => r.json()).catch(() => null);
    if (!T) return;
    $('tribunaForm').hidden = !T.abertas;
    $('tribunaFechada').hidden = T.abertas;
    el.innerHTML = T.inscritos.length ? `<ol>${T.inscritos.map(i => `<li><b>${esc(i.nome)}</b> · ${esc(i.entidade)}<div class="suave">${esc(i.tema)}</div></li>`).join('')}</ol>`
      : '<p class="vazio">Nenhuma inscrição aprovada ainda para a próxima sessão.</p>';
  }

  function ligarTribuna() {
    const f = $('tribunaForm'); if (!f) return;
    f.onsubmit = async ev => {
      ev.preventDefault();
      try { await enviar('/api/tribuna', Object.fromEntries(new FormData(f))); } catch (e) { return alert(e.message); }
      f.reset();
      alert('Inscrição recebida! A presidência confirma e define a ordem; os aprovados aparecem na lista.');
    };
  }

  const css = document.createElement('style');
  css.textContent = `.pc-placar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
    .form-contrib label.botao input{margin:0 4px 0 0}`;
  document.head.append(css);
  const iniciar = () => { pauta(); tribuna(); ligarTribuna(); setInterval(tribuna, 30000); };
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', iniciar) : iniciar();
  window.PautaCidada = { pauta, tribuna };
})();
