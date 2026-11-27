/* Painel do presidente: opinião do cidadão na pauta (placar e moderação), Tribuna Livre (aprovar e ordenar)
   e catálogo de normas municipais (LexML). Usa A, $, esc, apiGet, abaAtual e PL de presidente.js. */
(() => {
  const ICONE = { favor: '👍', contra: '👎', neutro: '✋' };
  const NOME = { favor: 'a favor', contra: 'contra', neutro: 'neutro' };
  const placarTxt = p => Object.keys(ICONE).map(k => `${ICONE[k]} ${p[k]}`).join(' · ');

  async function pauta() {
    const el = $('pcLista'); if (!el) return;
    const r = await (await apiGet('/api/pauta-cidada')).json().catch(() => null);
    if (!r) return;
    $('pcTelao').checked = !!r.telao;
    const pend = r.itens.reduce((n, m) => n + m.moderacao.filter(o => o.situacao === 'pendente').length, 0);
    $('pcEstado').innerHTML = pend ? `<span class="chip ambar">${pend} aguardando</span>` : '';
    el.innerHTML = r.itens.length ? r.itens.map(m => `<div class="contrib" style="margin-top:8px">
        <b>${esc(m.numero)}</b> <span class="suave">${esc(m.ementa)}</span>
        <div style="margin:4px 0"><span class="chip sim" title="Publicadas (aprovadas)">publicado: ${placarTxt(m.placar)}</span>
          <span class="chip" title="Inclui as que aguardam moderação">recebido: ${placarTxt(m.placarTotal)}</span>${m.aberto ? '' : ' <small class="suave">(não recebe mais opiniões)</small>'}</div>
        ${m.moderacao.filter(o => o.situacao !== 'recusado').map(o => `<div style="border-left:3px solid var(--linha);padding:4px 0 4px 10px;margin-top:6px">
          <span class="chip ${o.situacao === 'pendente' ? 'ambar' : 'sim'}">${o.situacao}</span> ${ICONE[o.posicao]} <b>${esc(o.nome)}</b> (${NOME[o.posicao]})
          <small class="suave">${esc(o.email)}${o.bairro ? ' · ' + esc(o.bairro) : ''}</small>
          <div style="white-space:pre-wrap;font-size:14px;margin:2px 0">${esc(o.texto)}</div>
          <div class="grupo">${o.situacao === 'pendente' ? `<button class="botao pq ok" onclick="A('opiniaoModerar',{id:'${esc(o.id)}',situacao:'aprovado'})">Aprovar</button>` : ''}
            <button class="botao pq fantasma" onclick="A('opiniaoModerar',{id:'${esc(o.id)}',situacao:'recusado'})">${o.situacao === 'pendente' ? 'Recusar' : 'Despublicar'}</button></div></div>`).join('')}
      </div>`).join('') : '<p class="vazio" style="margin:6px 0 0">Pauta vazia.</p>';
  }

  async function tribuna() {
    const el = $('tlLista'); if (!el) return;
    const r = await (await apiGet('/api/tribuna')).json().catch(() => null);
    if (!r) return;
    $('tlAbertas').checked = !!r.abertas;
    const todas = r.todas || [];
    const pend = todas.filter(i => i.situacao === 'pendente').length;
    $('tlEstado').innerHTML = pend ? `<span class="chip ambar">${pend} aguardando</span>` : '';
    el.innerHTML = todas.filter(i => i.situacao !== 'recusado').map(i => `<div class="contrib" style="margin-top:8px">
        <span class="chip ${i.situacao === 'pendente' ? 'ambar' : 'sim'}" style="float:right">${i.situacao === 'aprovado' ? i.ordem + 'º' : 'pendente'}</span>
        <b>${esc(i.nome)}</b> · ${esc(i.entidade)} <small class="suave">${esc(i.contato)}</small><div style="font-size:14px">${esc(i.tema)}</div>
        <div class="grupo" style="margin-top:4px">${i.situacao === 'pendente' ? `<button class="botao pq ok" onclick="A('tribunaModerar',{id:'${esc(i.id)}',situacao:'aprovado'})">Aprovar</button>`
          : `<button class="botao pq" onclick="A('tribunaMover',{id:'${esc(i.id)}',d:-1})" aria-label="Subir">↑</button><button class="botao pq" onclick="A('tribunaMover',{id:'${esc(i.id)}',d:1})" aria-label="Descer">↓</button>`}
          <button class="botao pq fantasma" onclick="A('tribunaModerar',{id:'${esc(i.id)}',situacao:'recusado'})">Recusar</button></div></div>`).join('')
      || '<p class="vazio" style="margin:6px 0 0">Nenhuma inscrição para esta sessão.</p>';
  }

  async function normas() {
    const el = $('nmLista'); if (!el) return;
    const r = await fetch('/api/normas-municipais').then(x => x.json()).catch(() => null);
    if (!r) return;
    if (!$('nmTipo').options.length) $('nmTipo').innerHTML = Object.entries(r.tipos).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('nmLocal').innerHTML = r.localidade ? `Localidade LexML: <code>${esc(r.localidade)}</code>` : '<span class="chip ambar">informe UF e município da Câmara para gerar as URNs municipais</span>';
    el.innerHTML = r.normas.length ? r.normas.slice(0, 200).map(n => `<div style="padding:6px 0;border-top:1px solid var(--linha);font-size:14px">
        <b>${esc(n.nome)}</b> <span class="suave">${esc(n.ementa || '')}</span><br><code style="font-size:12px">${esc(n.urn || '—')}</code>
        <button class="botao pq fantasma" style="float:right" onclick="A('normaMunicipalRemover',{id:'${esc(n.id)}'}).then(()=>PresCidadao.normas())">Remover</button></div>`).join('')
      + (r.normas.length > 200 ? `<p class="suave">… e mais ${r.normas.length - 200} (veja em <a href="/normas" target="_blank">/normas</a>).</p>` : '')
      : '<p class="vazio">Nenhuma norma cadastrada.</p>';
  }

  async function salvarNorma(f) {
    const d = Object.fromEntries(new FormData(f));
    await PL.acao('presidente', 'normaMunicipalSalvar', d).then(() => { f.reset(); normas(); PL.avisar('Norma salva.'); }).catch(() => {});
  }

  async function importar(f) {
    let csv = f.querySelector('textarea').value;
    const arq = f.querySelector('input[type=file]').files[0];
    if (arq) csv = await arq.text();
    if (!csv.trim()) return PL.avisar('Cole o CSV ou escolha o arquivo.', true);
    try {
      const r = await api('/api/normas-municipais/importar', { csv });
      PL.avisar(`${r.importadas} norma(s) importada(s).` + (r.erros.length ? ' Problemas: ' + r.erros.slice(0, 3).join(' | ') : ''), !!r.erros.length);
      f.reset(); normas();
    } catch (e) { PL.avisar(e.message, true); }
  }

  let rev = null;
  PL.aoMudar(S => {
    const chave = S.cidadaniaRev + ':' + (S.pauta || []).map(m => m.id + m.situacao).join(',');
    if (chave !== rev) { rev = chave; pauta(); tribuna(); }
  });
  document.querySelector('[data-aba="projetos"]')?.addEventListener('click', () => normas());
  window.PresCidadao = { pauta, tribuna, normas, salvarNorma, importar };
})();
