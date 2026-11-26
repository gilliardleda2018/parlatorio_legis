/* Busca pública nos discursos transcritos e publicados pela presidência.
   Discursos.montar(elemento, { pid }) — sem pid busca em todos os parlamentares. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const semAcento = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

  function realcar(texto, termos) {
    let html = esc(texto);
    if (!termos.length) return html;
    // marca os termos ignorando acentos: percorre o texto original procurando cada posição
    const base = semAcento(texto), marcas = [];
    for (const t of termos) for (let i = base.indexOf(t); i >= 0; i = base.indexOf(t, i + t.length)) marcas.push([i, i + t.length]);
    if (!marcas.length) return html;
    marcas.sort((a, b) => a[0] - b[0]);
    let out = '', pos = 0;
    for (const [a, b] of marcas) { if (a < pos) continue; out += esc(texto.slice(pos, a)) + '<mark>' + esc(texto.slice(a, b)) + '</mark>'; pos = b; }
    return out + esc(texto.slice(pos));
  }

  function montar(el, { pid = '', titulo = 'Discursos' } = {}) {
    el.innerHTML = `<h2>${esc(titulo)}</h2>
      <form class="disc-busca" role="search"><input class="campo" type="search" placeholder="Buscar nos discursos: saúde, estrada, escola…" aria-label="Buscar nos discursos">
      <button class="botao">Buscar</button></form>
      <form class="disc-busca disc-perg" role="search"><input class="campo" placeholder="Ou pergunte: o que foi dito sobre a estrada do povoado?" aria-label="Pergunte sobre os discursos" maxlength="300">
      <button class="botao">Perguntar</button></form>
      <div class="disc-resp" aria-live="polite"></div>
      <div class="disc-lista" aria-live="polite"><p class="vazio">Carregando…</p></div>`;
    const campo = el.querySelector('input'), lista = el.querySelector('.disc-lista');
    let termos = [];
    async function buscar() {
      const q = campo.value.trim();
      const r = await fetch(`/api/discursos?limite=20&q=${encodeURIComponent(q)}&pid=${encodeURIComponent(pid)}`).then(x => x.json()).catch(() => null);
      if (!r) { lista.innerHTML = '<p class="vazio">Não foi possível carregar os discursos.</p>'; return; }
      termos = r.termos;
      if (!r.itens.length) {
        lista.innerHTML = `<p class="vazio">${q ? `Nenhum discurso publicado fala em “${esc(q)}”.` : 'Nenhum discurso transcrito foi publicado ainda.'}</p>`;
        return;
      }
      lista.innerHTML = (q ? `<p class="detalhe" style="margin:0 0 8px">${r.total} discurso(s) encontrado(s)</p>` : '') + r.itens.map(d => `
        <article class="disc" data-id="${d.id}">
          <div class="disc-cab">${pid ? '' : `<a href="/parlamentares/${d.pid}"><b>${esc(d.nome)}</b></a> <span class="suave">${esc(d.partido)}</span> · `}
            <span class="suave">${esc(d.sessao)}${d.fase ? ' · ' + esc(d.fase) : ''} · ${new Date(d.inicio).toLocaleDateString('pt-BR')}</span></div>
          <p class="disc-trecho">${realcar(d.trecho, termos)}</p>
          <div class="disc-acoes"><button class="botao pq fantasma" data-ler>Ler a íntegra (${d.palavras} palavras)</button>
            <audio controls preload="none" src="${d.audio}"></audio>
            ${d.revisada ? '' : '<small class="suave">transcrição automática, pode conter erros — vale o áudio</small>'}</div>
        </article>`).join('');
    }
    lista.addEventListener('click', async ev => {
      const b = ev.target.closest('[data-ler]'); if (!b) return;
      const art = b.closest('.disc');
      const r = await fetch('/api/transcricao/' + art.dataset.id).then(x => x.ok ? x.json() : null).catch(() => null);
      if (!r) return;
      art.querySelector('.disc-trecho').innerHTML = r.transcricao.texto.split(/\n\s*\n|\n/).filter(Boolean).map(p => `<span class="disc-par">${realcar(p, termos)}</span>`).join('');
      b.remove();
    });
    el.querySelector('form').addEventListener('submit', ev => { ev.preventDefault(); buscar(); });
    el.querySelector('.disc-perg').addEventListener('submit', async ev => {
      ev.preventDefault();
      const campo = ev.target.querySelector('input'), resp = el.querySelector('.disc-resp');
      if (campo.value.trim().length < 4) return;
      resp.innerHTML = '<p class="detalhe">Procurando nos discursos…</p>';
      const r = await fetch('/api/perguntar', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pergunta: (pid ? 'Discursos deste parlamentar: ' : '') + campo.value, escopo: 'discursos' }) }).then(x => x.json()).catch(() => ({ erro: 'Sem conexão.' }));
      if (r.erro) { resp.innerHTML = `<p class="detalhe">${esc(r.erro)}</p>`; return; }
      const fontes = (r.fontes || []).map((f, k) => `<div class="disc-cab" style="margin-top:6px">[${k + 1}] <a href="${esc(f.link || '#')}">${esc(f.titulo)}</a> — <span class="suave">${esc(f.texto.slice(0, 220))}…</span></div>`).join('');
      resp.innerHTML = `<div class="cartao neutro" style="margin:8px 0">${r.resposta ? `<p style="margin:0 0 6px">${esc(r.resposta)}</p>` : ''}${fontes}
        ${r.modo === 'ia' ? '<small class="suave">Resposta automática a partir dos discursos publicados; confira nas fontes.</small>' : ''}</div>`;
    });
    buscar();
  }

  const css = document.createElement('style');
  css.textContent = `.disc-busca{display:flex;gap:8px;margin-bottom:12px}.disc-busca input{flex:1}
    .disc{padding:12px 0;border-top:1px solid var(--linha)}.disc-cab{font-size:14px}.disc-cab a{color:inherit}
    .disc-trecho{margin:6px 0;line-height:1.55;max-width:75ch}.disc-par{display:block;margin-bottom:8px}
    .disc-trecho mark{background:color-mix(in srgb,var(--destaque,#1f8a4c) 30%,transparent);color:inherit;border-radius:3px;padding:0 2px}
    .disc-acoes{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.disc-acoes audio{height:32px;max-width:100%}`;
  document.head.append(css);
  window.Discursos = { montar };
})();
