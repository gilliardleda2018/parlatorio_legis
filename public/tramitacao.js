/* Tramitação: protocolo eletrônico, comissões, prazos regimentais e pedidos ao Executivo.
   Incluído no painel do presidente (aba "Tramitação") e no app do vereador (aba "Protocolo").
   A aba é criada por este arquivo; tudo o que vem de usuário passa por PL.esc / PL.urlSegura. */
(() => {
  const esc = PL.esc;
  const DIA = 864e5;
  const dataBR = ts => ts ? new Date(ts).toLocaleDateString('pt-BR') : '—';
  const dataHora = ts => ts ? new Date(ts).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const COR_ETAPA = { recebida: 'ambar', devolvida: 'nao', em_comissao: 'ambar', pronta_pauta: 'sim', votada: 'sim', sancao: 'sim' };
  const COR_EXEC = { aguardando: 'ambar', em_atraso: 'nao', respondido: 'sim' };
  const chipEtapa = m => `<span class="chip ${COR_ETAPA[m.etapa] || ''}">${esc(m.etapaNome)}${m.resultado && ['votada', 'sancao', 'arquivada'].includes(m.etapa) ? ' · ' + (m.resultado === 'aprovada' ? 'aprovada' : 'rejeitada') : ''}</span>`;
  const chipExec = ex => ex ? `<span class="chip ${COR_EXEC[ex.situacao] || ''}">${esc(ex.situacaoNome)}${ex.resposta ? (ex.resposta.noPrazo ? ' no prazo' : ' fora do prazo') : ''}</span>` : '';
  function prazo(vence, agora, alerta = 5) {
    if (!vence) return '';
    const d = Math.ceil((vence - agora) / DIA);
    if (d < 0) return `<span class="chip nao">${-d} dia(s) em atraso</span>`;
    if (d <= alerta) return `<span class="chip ambar">vence em ${d} dia(s)</span>`;
    return `<span class="suave" style="font-size:12px">prazo: ${dataBR(vence)}</span>`;
  }
  const lerPdf = arquivo => new Promise((ok, falha) => {
    if (!arquivo) return ok('');
    if (arquivo.size > 2_800_000) return falha(new Error('PDF maior que 2,8 MB.'));
    const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => falha(new Error('Não foi possível ler o arquivo.')); r.readAsDataURL(arquivo);
  });
  const opcoesTipos = (tipos, atual) => tipos.map(t => `<option value="${esc(t.sigla)}" ${t.sigla === atual ? 'selected' : ''}>${esc(t.sigla)} — ${esc(t.nome)}</option>`).join('');
  const linkPublico = id => `/tramitacao#${encodeURIComponent(id)}`;

  /* =================================================================== painel do presidente */
  function painelPresidente() {
    const $ = id => document.getElementById(id);
    const ref = document.querySelector('.nav .menu [data-aba="projetos"]');
    const bt = document.createElement('button');
    bt.className = 'item'; bt.dataset.aba = 'tramitacao';
    bt.innerHTML = 'Tramitação <span class="bolha" id="bolhaTram"></span>';
    ref.after(bt);
    const sec = document.createElement('section');
    sec.className = 'aba'; sec.id = 'aba-tramitacao';
    document.querySelector('#app main').append(sec);
    let D = null, chave = null, espera = null, aberto = null;
    const filtro = { q: '', etapa: '' };

    bt.onclick = () => {
      abaAtual = 'tramitacao';  // variável do presidente.js
      document.querySelectorAll('[data-aba]').forEach(x => x.classList.toggle('ativo', x === bt));
      document.querySelectorAll('.aba').forEach(s => s.classList.toggle('ativa', s === sec));
      desenhar(); carregar();
    };
    const logado = () => PL.token('presidente') && !$('app').classList.contains('oculto');
    PL.aoMudar(S => {
      const k = S.projetosRev + ':' + JSON.stringify(S.tramitacao || {});
      if (k === chave || !logado()) return;
      chave = k; clearTimeout(espera); espera = setTimeout(carregar, 250);
    });

    async function carregar() {
      if (!logado()) return;
      const r = await fetch('/api/tramitacao/painel', { headers: { Authorization: 'Bearer ' + PL.token('presidente') } });
      if (!r.ok) return;
      const primeira = !D;
      D = await r.json();
      const atrasados = D.alertas.filter(a => a.atrasado).length, n = D.alertas.length + D.propostas.length;
      $('bolhaTram').textContent = n || '';
      $('bolhaTram').style.background = atrasados ? 'var(--vermelho)' : '';
      $('bolhaTram').title = `${D.alertas.length} prazo(s) vencendo ou vencido(s) · ${D.propostas.length} proposta(s) aguardando protocolo`;
      if (primeira && atrasados) PL.avisar(`Tramitação: ${atrasados} prazo(s) vencido(s). Veja a aba Tramitação.`, true);
      if (abaAtual === 'tramitacao') desenharAba();
      if (aberto && $('dlg').open) desenharTramitar(aberto);
    }
    const A2 = async (nome, args) => { await PL.acao('presidente', nome, args); await carregar(); };
    const nomeParl = pid => PL.parl(pid).nome;
    const comissao = cid => D.cfg.comissoes.find(c => c.id === cid) || { nome: '(removida)', membros: [] };

    function desenharAba() {
      const ag = D.agora, al = D.cfg.prazos.alerta, pl = D.placar;
      const abertas = D.cfg.reunioes.filter(r => !r.fim);
      sec.innerHTML = `
      ${D.alertas.length ? `<div class="cartao" style="border-top-color:var(--vermelho);margin-bottom:16px"><h2>Prazos vencendo ou vencidos<span class="esp"></span><span class="conta">${D.alertas.length}</span></h2>
        <table class="tab">${D.alertas.map(a => `<tr><td><b>${esc(a.numero)}</b><div style="font-size:13px">${esc(a.ementa)}</div></td><td>${esc(a.texto)}</td>
          <td>${prazo(a.vence, ag, al)}</td><td style="text-align:right"><button class="botao pq" data-t="tramitar" data-id="${esc(a.materia)}">Abrir</button></td></tr>`).join('')}</table></div>` : ''}
      <div class="grade2">
        <div class="coluna">
          <div class="cartao">
            <h2>Propostas aguardando protocolo<span class="esp"></span><button class="botao pq principal" data-t="nova">+ Protocolar matéria</button></h2>
            ${D.propostas.length ? `<table class="tab">${D.propostas.map(p => `<tr><td><b>${esc(p.tipo || p.numero)}</b><div style="font-size:13px">${esc(p.ementa)}</div>
              <small class="suave">${esc(p.autor)} · ${dataHora(p.etapaEm || p.criado)}</small></td><td>${chipEtapa(p)}</td>
              <td style="text-align:right"><button class="botao pq principal" data-t="conferir" data-id="${esc(p.id)}">Conferir</button></td></tr>`).join('')}</table>`
              : '<p class="vazio" style="margin:0">Nenhuma proposta aguardando. Os vereadores protocolam pelo app (aba Protocolo).</p>'}
          </div>
          <div class="cartao neutro">
            <h2>Pedidos ao Executivo</h2>
            <div class="numeros" style="margin:0"><div><b>${pl.enviados}</b>enviados</div><div><b>${pl.noPrazo}</b>respondidos no prazo</div>
              <div><b>${pl.foraDoPrazo}</b>fora do prazo</div><div><b>${pl.aguardando}</b>aguardando</div><div><b style="color:var(--vermelho)">${pl.emAtraso}</b>em atraso</div></div>
            <p class="detalhe" style="margin-bottom:0">Placar público em <a href="/tramitacao" target="_blank" rel="noopener">/tramitacao</a>. Prazo padrão: ${D.cfg.prazos.executivo} dias.</p>
          </div>
        </div>
        <div class="coluna">
          <div class="cartao neutro">
            <h2>Comissões permanentes<span class="esp"></span><button class="botao pq" data-t="comissao">+ Comissão</button></h2>
            ${D.cfg.comissoes.length ? `<table class="tab">${D.cfg.comissoes.map(c => `<tr><td><b>${esc(c.nome)}</b><div style="font-size:12px" class="suave">${c.membros.map(m => esc(nomeParl(m)) + (m === c.presidente ? ' (presidente)' : '')).join(', ')}</div></td>
              <td style="text-align:right;white-space:nowrap"><button class="botao pq" data-t="comissao" data-id="${esc(c.id)}">Editar</button>
              <button class="botao pq fantasma" data-t="remComissao" data-id="${esc(c.id)}">✕</button></td></tr>`).join('')}</table>`
              : '<p class="vazio" style="margin:0">Cadastre as comissões (ex.: Constituição e Justiça; Finanças e Orçamento).</p>'}
          </div>
          <div class="cartao neutro">
            <h2>Reuniões de comissão</h2>
            <div class="grupo"><select class="campo" id="trReuCom" style="flex:1">${D.cfg.comissoes.map(c => `<option value="${esc(c.id)}">${esc(c.nome)}</option>`).join('')}</select>
              <button class="botao pq principal" data-t="abrirReuniao" ${D.cfg.comissoes.length ? '' : 'disabled'}>Abrir reunião</button></div>
            ${abertas.map(r => desenharReuniao(r)).join('')}
          </div>
        </div>
      </div>
      <div class="cartao neutro" style="margin-top:16px">
        <h2>Matérias em tramitação<span class="esp"></span><span class="conta">${D.materias.length}</span></h2>
        <div class="grupo" style="margin-bottom:10px">
          <input class="campo" id="trBusca" placeholder="Buscar número, ementa ou autor…" style="flex:1;min-width:200px" value="${esc(filtro.q)}">
          <select class="campo" id="trEtapa" style="width:auto"><option value="">Todas as etapas</option>${Object.entries(D.etapas).filter(([k]) => !['recebida', 'devolvida'].includes(k)).map(([k, v]) => `<option value="${k}" ${filtro.etapa === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>
        </div>
        <table class="tab" id="trLista"></table>
      </div>
      <div class="grade2" style="margin-top:16px">
        <div class="cartao neutro">
          <h2>Matérias sem tramitação eletrônica</h2>
          <p class="detalhe" style="margin-top:0">Antigas ou importadas do SAPL. Continuam indo à pauta normalmente; para acompanhar prazos e comissões aqui, inicie a tramitação (o número é mantido).</p>
          ${D.semTramitacao.length ? `<table class="tab">${D.semTramitacao.slice(0, 60).map(p => `<tr><td><b>${esc(p.numero)}</b><div style="font-size:13px">${esc(p.ementa)}</div>
            ${PL.urlSegura(p.saplUrl) ? `<a class="detalhe" href="${esc(PL.urlSegura(p.saplUrl))}" target="_blank" rel="noopener noreferrer">SAPL ↗</a>` : ''}</td>
            <td style="text-align:right"><button class="botao pq" data-t="iniciar" data-id="${esc(p.id)}">Iniciar tramitação</button></td></tr>`).join('')}</table>`
            : '<p class="vazio" style="margin:0">Nenhuma.</p>'}
        </div>
        <div class="cartao neutro">
          <h2>Tipos de matéria e prazos</h2>
          <table class="tab" id="trTipos"><tr><th>Sigla</th><th>Nome</th><th title="Pedido de informação, indicação: vai à prefeitura com prazo de resposta">Ao Executivo</th><th></th></tr>
            ${D.cfg.tipos.map(t => linhaTipo(t)).join('')}</table>
          <div class="grupo" style="margin:8px 0"><button class="botao pq" data-t="maisTipo">+ Tipo</button></div>
          ${[['parecer', 'Parecer do relator'], ['vista', 'Pedido de vista'], ['executivo', 'Resposta do Executivo (Lei Orgânica)'], ['alerta', 'Avisar quantos dias antes']].map(([k, v]) =>
            `<label class="linha">${v} (dias)<input class="campo" type="number" min="1" max="365" data-prazo="${k}" value="${D.cfg.prazos[k]}" style="width:90px"></label>`).join('')}
          <div class="grupo" style="justify-content:flex-end;margin-top:10px"><button class="botao principal" data-t="salvarConfig">Salvar tipos e prazos</button></div>
        </div>
      </div>`;
      desenharLista();
      $('trBusca').oninput = e => { filtro.q = e.target.value; desenharLista(); };
      $('trEtapa').onchange = e => { filtro.etapa = e.target.value; desenharLista(); };
    }
    const linhaTipo = t => `<tr data-tipo><td><input class="campo" data-c="sigla" value="${esc(t.sigla)}" maxlength="6" style="width:80px;text-transform:uppercase"></td>
      <td><input class="campo" data-c="nome" value="${esc(t.nome)}" maxlength="80"></td><td style="text-align:center"><input type="checkbox" data-c="executivo" ${t.executivo ? 'checked' : ''}></td>
      <td><button class="botao pq fantasma" data-t="remTipo">✕</button></td></tr>`;

    function desenharLista() {
      const q = filtro.q.toLowerCase(), ag = D.agora, al = D.cfg.prazos.alerta;
      const lista = D.materias.filter(m => (!q || `${m.numero} ${m.ementa} ${m.autor}`.toLowerCase().includes(q)) && (!filtro.etapa || m.etapa === filtro.etapa));
      $('trLista').innerHTML = lista.length ? '<tr><th>Matéria</th><th>Etapa</th><th>Comissões</th><th>Executivo</th><th></th></tr>' + lista.map(m => `<tr>
        <td><b>${esc(m.numero)}</b> <span class="suave" style="font-size:12px">${esc(m.tipoNome)}</span><div style="font-size:13px;max-width:420px">${esc(m.ementa)}</div><small class="suave">${esc(m.autor)}</small></td>
        <td>${chipEtapa(m)}<div class="suave" style="font-size:12px">desde ${dataBR(m.etapaEm)}</div></td>
        <td style="font-size:13px">${m.comissoes.map(c => `<div>${esc(c.nome)}: ${esc(c.situacaoNome)}${c.situacao === 'com_relator' ? ' ' + prazo(c.prazoParecer, ag, al) : ''}${c.situacao === 'vista' && c.vista ? ' ' + prazo(c.vista.prazo, ag, al) : ''}</div>`).join('') || '—'}</td>
        <td>${m.executivo ? chipExec(m.executivo) + (m.executivo.resposta ? '' : ' ' + prazo(m.executivo.vence, ag, al)) : m.paraExecutivo ? '<span class="suave" style="font-size:12px">não enviado</span>' : '—'}</td>
        <td style="text-align:right;white-space:nowrap"><button class="botao pq principal" data-t="tramitar" data-id="${esc(m.id)}">Tramitar</button>
          ${m.protocolo ? `<a class="botao pq" href="/api/tramitacao/comprovante/${encodeURIComponent(m.id)}.pdf" target="_blank" rel="noopener">Comprovante</a>` : ''}
          <a class="botao pq fantasma" href="${linkPublico(m.id)}" target="_blank" rel="noopener">Pública ↗</a></td></tr>`).join('')
        : '<tr><td class="vazio">Nenhuma matéria.</td></tr>';
    }

    function desenharReuniao(r) {
      const c = comissao(r.comissao);
      const pauta = D.materias.filter(m => m.comissoes.some(x => x.comissao === r.comissao && x.situacao === 'parecer'));
      return `<div class="cartao" style="margin-top:12px;padding:12px" data-reuniao="${esc(r.id)}">
        <div class="grupo" style="justify-content:space-between"><b>${esc(c.nome)}</b><span class="suave" style="font-size:12px">aberta ${dataHora(r.inicio)}${c.presidente ? ' · presidente: ' + esc(nomeParl(c.presidente)) : ''}</span></div>
        <div class="rot" style="margin-top:8px">Presença (${r.presentes.length}/${c.membros.length}; quórum ${Math.floor(c.membros.length / 2) + 1})
          <div class="grupo">${c.membros.map(m => `<label style="display:inline-flex;gap:6px;align-items:center;font-size:13px;color:var(--texto)"><input type="checkbox" data-t="presenca" data-r="${esc(r.id)}" data-pid="${esc(m)}" ${r.presentes.includes(m) ? 'checked' : ''}>${esc(nomeParl(m))}</label>`).join('')}</div></div>
        ${pauta.length ? pauta.map(m => { const x = m.comissoes.find(y => y.comissao === r.comissao); return `<div style="border-top:1px solid var(--linha);margin-top:8px;padding-top:8px" data-votos="${esc(m.id)}">
          <b>${esc(m.numero)}</b> · parecer ${esc(x.parecer?.votoNome || '')} (relator ${esc(x.relator || '—')})
          <div class="grupo" style="margin-top:6px">${r.presentes.map(pid => `<label style="font-size:13px;display:inline-flex;gap:4px;align-items:center">${esc(nomeParl(pid))}
            <select class="campo" data-voto="${esc(pid)}" style="width:auto;padding:2px 6px"><option value="">—</option><option value="sim">Sim</option><option value="nao">Não</option><option value="abst">Abst.</option></select></label>`).join('') || '<span class="suave">Marque a presença dos membros.</span>'}</div>
          <button class="botao pq principal" style="margin-top:6px" data-t="votarReuniao" data-r="${esc(r.id)}" data-id="${esc(m.id)}">Registrar votação do parecer</button></div>`; }).join('')
          : '<p class="detalhe" style="margin:8px 0 0">Nenhum parecer pronto para votação nesta comissão.</p>'}
        ${r.itens.length ? `<div class="detalhe" style="margin-top:8px">${r.itens.map(i => `${esc(i.numero)}: parecer ${i.aprovado ? 'aprovado' : 'rejeitado'} (${i.sim}×${i.nao})`).join('<br>')}</div>` : ''}
        <div class="grupo" style="justify-content:flex-end;margin-top:8px"><button class="botao pq perigo" data-t="encerrarReuniao" data-r="${esc(r.id)}">Encerrar reunião</button></div></div>`;
    }

    /* ----- diálogos ----- */
    function conferir(id) {
      const p = D.propostas.find(x => x.id === id); if (!p) return;
      dlg.classList.add('largo');
      dialogo('Conferir e protocolar', `
        <p class="detalhe" style="margin:0">${esc(p.numero)} · ${esc(p.autor)} · enviada ${dataHora(p.etapaEm || p.criado)}${p.instrumento ? ' · instrumento sugerido: ' + esc(p.instrumento) : ''}</p>
        <label class="rot">Tipo (define a numeração)<select class="campo" id="trTipo"><option value="">Escolha…</option>${opcoesTipos(D.cfg.tipos, p.tipo)}</select></label>
        <div class="rot">Ementa<div style="color:var(--texto)">${esc(p.ementa)}</div></div>
        ${p.texto ? `<div class="rot">Texto<div style="color:var(--texto);white-space:pre-wrap;max-height:260px;overflow:auto;font-size:13px">${esc(p.texto)}</div></div>` : '<p class="detalhe" style="margin:0">Sem texto anexado: o hash do protocolo será calculado sobre a ementa.</p>'}
        ${p.justificativa ? `<div class="rot">Justificativa<div style="color:var(--texto);white-space:pre-wrap;font-size:13px">${esc(p.justificativa)}</div></div>` : ''}
        ${p.etapa === 'devolvida' ? `<p class="detalhe" style="margin:0">Devolvida: ${esc(p.motivo)}</p>` : `
        <details><summary class="detalhe" style="cursor:pointer">Devolver ao autor para correção</summary>
          <label class="rot">Motivo<textarea class="campo" id="trMotivo" rows="2" maxlength="1000"></textarea></label>
          <button class="botao pq perigo" data-t="devolver" data-id="${esc(p.id)}">Devolver</button></details>`}`,
        async () => { await A2('tramProtocolar', { id, tipo: $('trTipo').value }); PL.avisar('Matéria protocolada.'); }, 'Protocolar');
      dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
    }
    function nova() {
      dlg.classList.add('largo');
      dialogo('Protocolar matéria', `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <label class="rot">Tipo<select class="campo" id="trTipo">${opcoesTipos(D.cfg.tipos, 'PL')}</select></label>
          <label class="rot">Autoria<select class="campo" id="trAutorPid"><option value="">Outra (escreva ao lado)…</option>${[...PL.estado.parls].sort((a, b) => a.nome.localeCompare(b.nome)).map(p => `<option value="${esc(p.id)}">${esc(p.nome)}</option>`).join('')}</select></label>
        </div>
        <label class="rot">Outra autoria<input class="campo" id="trAutor" maxlength="120" placeholder="Executivo Municipal, Mesa Diretora, comissão…"></label>
        <label class="rot">Ementa<textarea class="campo" id="trEmenta" rows="2" maxlength="1000"></textarea></label>
        <label class="rot">Texto<textarea class="campo" id="trTexto" rows="8" maxlength="100000"></textarea></label>
        <label class="rot">Justificativa<textarea class="campo" id="trJust" rows="3" maxlength="20000"></textarea></label>`,
        async () => {
          await A2('tramNova', { tipo: $('trTipo').value, ementa: $('trEmenta').value, texto: $('trTexto').value, justificativa: $('trJust').value,
            autorPid: $('trAutorPid').value || null, autor: $('trAutor').value });
          PL.avisar('Matéria protocolada.');
        }, 'Protocolar');
      dlg.addEventListener('close', () => dlg.classList.remove('largo'), { once: true });
    }
    function editarComissao(id) {
      const c = id ? D.cfg.comissoes.find(x => x.id === id) : { nome: '', membros: [], presidente: null };
      dialogo(id ? 'Editar comissão' : 'Nova comissão', `
        <label class="rot">Nome<input class="campo" id="trComNome" value="${esc(c.nome)}" maxlength="120" placeholder="Comissão de Constituição, Justiça e Redação"></label>
        <div class="rot">Membros<div class="opcoes">${[...PL.estado.parls].sort((a, b) => a.nome.localeCompare(b.nome)).map(p => `<label><input type="checkbox" name="trMembro" value="${esc(p.id)}" ${c.membros.includes(p.id) ? 'checked' : ''} hidden>${esc(p.nome)}</label>`).join('')}</div></div>
        <label class="rot">Presidente da comissão<select class="campo" id="trComPres"><option value="">—</option>${PL.estado.parls.map(p => `<option value="${esc(p.id)}" ${p.id === c.presidente ? 'selected' : ''}>${esc(p.nome)}</option>`).join('')}</select></label>`,
        () => A2('tramComissaoSalvar', { id: id || null, nome: $('trComNome').value, presidente: $('trComPres').value || null,
          membros: [...document.querySelectorAll('[name="trMembro"]:checked')].map(x => x.value) }));
    }

    function desenharTramitar(id) {
      const m = D.materias.find(x => x.id === id);
      if (!m) { dlg.close(); return; }
      const ag = D.agora, al = D.cfg.prazos.alerta;
      const destinos = (D.transicoes[m.etapa] || []);
      const podeComissao = ['protocolada', 'lida', 'em_comissao', 'pronta_pauta'].includes(m.etapa);
      const blocoComissao = x => { const c = comissao(x.comissao); return `<div class="cartao neutro" style="padding:10px;margin:0">
        <div class="grupo" style="justify-content:space-between"><b>${esc(x.nome)}</b><span class="chip ${x.situacao === 'concluida' ? 'sim' : x.situacao === 'parecer_rejeitado' ? 'nao' : 'ambar'}">${esc(x.situacaoNome)}</span></div>
        <div class="detalhe">Entrada ${dataBR(x.entrada)}${x.relator ? ` · relator: <b>${esc(x.relator)}</b>` : ''}${x.situacao === 'com_relator' ? ' ' + prazo(x.prazoParecer, ag, al) : ''}</div>
        ${x.parecer ? `<details style="font-size:13px"><summary>Parecer ${esc(x.parecer.votoNome)} · ${dataBR(x.parecer.ts)}${x.parecer.noPrazo ? '' : ' (fora do prazo)'}</summary><div style="white-space:pre-wrap">${esc(x.parecer.texto)}</div></details>` : ''}
        ${x.vista ? `<div class="detalhe">Vista: ${esc(x.vista.nome)} desde ${dataBR(x.vista.ts)}${x.vista.devolvida ? ' · devolvida ' + dataBR(x.vista.devolvida) : ' ' + prazo(x.vista.prazo, ag, al)}</div>` : ''}
        ${x.votacao ? `<div class="detalhe">Votação na comissão: ${x.votacao.sim.length} sim × ${x.votacao.nao.length} não</div>` : ''}
        ${m.etapa === 'em_comissao' && x.situacao !== 'concluida' ? `
          <div class="grupo" style="margin-top:6px"><select class="campo" data-relator="${esc(x.comissao)}" style="width:auto">${c.membros.map(p => `<option value="${esc(p)}" ${p === x.relatorPid ? 'selected' : ''}>${esc(nomeParl(p))}</option>`).join('')}</select>
            <button class="botao pq" data-t="relator" data-c="${esc(x.comissao)}">${x.relatorPid ? 'Trocar relator' : 'Designar relator'}</button></div>
          ${['com_relator', 'parecer'].includes(x.situacao) ? `<details style="margin-top:6px"><summary class="detalhe" style="cursor:pointer">Registrar parecer pelo relator</summary>
            <div class="grupo"><select class="campo" data-parecer-voto="${esc(x.comissao)}" style="width:auto">${Object.entries(D.votosParecer).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></div>
            <textarea class="campo" data-parecer-texto="${esc(x.comissao)}" rows="3" maxlength="50000" placeholder="Texto do parecer"></textarea>
            <button class="botao pq" data-t="parecer" data-c="${esc(x.comissao)}">Registrar parecer</button></details>` : ''}
          ${x.situacao === 'parecer' ? `<div class="grupo" style="margin-top:6px"><select class="campo" data-vista="${esc(x.comissao)}" style="width:auto">${c.membros.map(p => `<option value="${esc(p)}">${esc(nomeParl(p))}</option>`).join('')}</select>
            <button class="botao pq" data-t="vista" data-c="${esc(x.comissao)}">Pedido de vista</button></div>` : ''}
          ${x.situacao === 'vista' ? `<button class="botao pq" style="margin-top:6px" data-t="devolverVista" data-c="${esc(x.comissao)}">Devolver vista</button>` : ''}` : ''}
      </div>`; };
      const rotulo = { lida: 'Lida em plenário', pronta_pauta: 'Pronta para a pauta', votada: 'Registrar votação', sancao: 'Enviar à sanção', arquivada: 'Arquivar' };
      const ex = m.executivo;
      $('dlgTitulo').textContent = `${m.numero} — tramitação`;
      $('dlgCorpo').innerHTML = `
        <p style="margin:0">${esc(m.ementa)}</p>
        <p class="detalhe" style="margin:0">${esc(m.tipoNome)} · ${esc(m.autor)}${m.protocolo ? ` · protocolada ${dataHora(m.protocolo.ts)}` : ''} · ${chipEtapa(m)}</p>
        ${destinos.length ? `<div class="rot">Avançar<div class="grupo">${destinos.map(e => e === 'votada'
          ? `<button class="botao pq" data-t="avancar" data-e="votada" data-res="aprovada">Votada: aprovada</button><button class="botao pq" data-t="avancar" data-e="votada" data-res="rejeitada">Votada: rejeitada</button>`
          : `<button class="botao pq ${e === 'arquivada' ? 'perigo' : ''}" data-t="avancar" data-e="${e}">${esc(rotulo[e] || D.etapas[e])}</button>`).join('')}
          ${m.etapa === 'em_comissao' ? '<label style="font-size:13px;display:inline-flex;gap:6px;align-items:center"><input type="checkbox" id="trForcar"> dispensar parecer pendente</label>' : ''}</div>
          <input class="campo" id="trObs" maxlength="1000" placeholder="Observação (opcional)" style="margin-top:6px"></div>` : ''}
        ${podeComissao && D.cfg.comissoes.length ? `<div class="rot">Distribuir às comissões<div class="opcoes">${D.cfg.comissoes.map(c => `<label><input type="checkbox" name="trDist" value="${esc(c.id)}" hidden>${esc(c.nome)}</label>`).join('')}</div>
          <div><button class="botao pq" data-t="distribuir">Distribuir</button></div></div>` : ''}
        ${m.comissoes.length ? `<div class="rot">Comissões<div class="coluna" style="gap:8px">${m.comissoes.map(blocoComissao).join('')}</div></div>` : ''}
        ${m.paraExecutivo && !ex && !['arquivada'].includes(m.etapa) ? `<div class="rot">Enviar ao Executivo
          <div class="grupo"><input class="campo" id="trOficio" maxlength="120" placeholder="Nº do ofício (opcional)" style="flex:1">
          <label style="font-size:13px">Prazo <input class="campo" type="number" id="trPrazo" min="1" max="365" value="${D.cfg.prazos.executivo}" style="width:80px"> dias</label>
          <button class="botao pq principal" data-t="enviarExec">Registrar envio</button></div></div>` : ''}
        ${ex ? `<div class="rot">Executivo<div style="color:var(--texto);font-size:13px">Enviado ${dataBR(ex.enviado)}${ex.oficio ? ' (' + esc(ex.oficio) + ')' : ''} · prazo ${ex.prazoDias} dias, até ${dataBR(ex.vence)} · ${chipExec(ex)} ${ex.resposta ? '' : prazo(ex.vence, ag, al)}</div>
          ${ex.resposta ? `<div style="white-space:pre-wrap;font-size:13px;color:var(--texto)">${esc(ex.resposta.texto)}</div>${ex.resposta.anexo ? `<a class="detalhe" href="/api/tramitacao/resposta/${encodeURIComponent(m.id)}.pdf" target="_blank" rel="noopener">📄 ${esc(ex.resposta.anexo.nome)}</a>` : ''}`
          : `<textarea class="campo" id="trResp" rows="3" maxlength="50000" placeholder="Resumo da resposta da prefeitura"></textarea>
            <div class="grupo"><label style="font-size:13px">Recebida em <input class="campo" type="date" id="trRespData" style="width:auto"></label>
            <input type="file" id="trRespPdf" accept="application/pdf" style="font-size:13px">
            <button class="botao pq principal" data-t="respostaExec">Registrar resposta</button></div>`}</div>` : ''}
        <div class="grupo">${m.protocolo ? `<a class="botao pq" href="/api/tramitacao/comprovante/${encodeURIComponent(m.id)}.pdf" target="_blank" rel="noopener">Comprovante de protocolo</a>` : ''}
          <a class="botao pq fantasma" href="${linkPublico(m.id)}" target="_blank" rel="noopener">Linha do tempo pública ↗</a></div>`;
    }
    function tramitar(id) {
      aberto = id;
      dlg.classList.add('largo');
      dialogo('', '', () => {}, 'Fechar');
      desenharTramitar(id);
      dlg.addEventListener('close', () => { aberto = null; dlg.classList.remove('largo'); }, { once: true });
    }

    /* ----- cliques (delegados: nada de dado de usuário em onclick) ----- */
    const valor = sel => document.querySelector(sel)?.value;
    const acoes = {
      nova, conferir: d => conferir(d.id), tramitar: d => tramitar(d.id), comissao: d => editarComissao(d.id),
      remComissao: d => confirm('Remover esta comissão?') && A2('tramComissaoRemover', { id: d.id }),
      iniciar: d => A2('tramIniciar', { id: d.id }),
      devolver: async d => { await A2('tramDevolver', { id: d.id, motivo: $('trMotivo').value }); dlg.close(); PL.avisar('Devolvida ao autor.'); },
      abrirReuniao: () => A2('tramReuniaoAbrir', { comissao: $('trReuCom').value }),
      encerrarReuniao: d => confirm('Encerrar a reunião?') && A2('tramReuniaoEncerrar', { id: d.r }),
      votarReuniao: d => {
        const votos = {};
        document.querySelectorAll(`[data-reuniao="${CSS.escape(d.r)}"] [data-votos="${CSS.escape(d.id)}"] [data-voto]`).forEach(s => { if (s.value) votos[s.dataset.voto] = s.value; });
        return A2('tramReuniaoVotar', { id: d.r, materia: d.id, votos });
      },
      maisTipo: () => $('trTipos').insertAdjacentHTML('beforeend', linhaTipo({ sigla: '', nome: '', executivo: false })),
      remTipo: (d, b) => b.closest('tr').remove(),
      salvarConfig: () => A2('tramConfigurar', {
        tipos: [...document.querySelectorAll('#trTipos [data-tipo]')].map(tr => ({ sigla: tr.querySelector('[data-c="sigla"]').value.trim().toUpperCase(),
          nome: tr.querySelector('[data-c="nome"]').value, executivo: tr.querySelector('[data-c="executivo"]').checked })),
        prazos: Object.fromEntries([...document.querySelectorAll('[data-prazo]')].map(i => [i.dataset.prazo, +i.value])) }).then(() => PL.avisar('Tipos e prazos salvos.')),
      // dentro do diálogo de tramitação
      avancar: d => A2('tramAvancar', { id: aberto, etapa: d.e, resultado: d.res || null, obs: valor('#trObs') || '', forcar: !!$('trForcar')?.checked }),
      distribuir: () => A2('tramDistribuir', { id: aberto, comissoes: [...document.querySelectorAll('[name="trDist"]:checked')].map(x => x.value) }),
      relator: d => A2('tramRelator', { id: aberto, comissao: d.c, relator: valor(`[data-relator="${CSS.escape(d.c)}"]`) }),
      parecer: d => A2('tramParecer', { id: aberto, comissao: d.c, voto: valor(`[data-parecer-voto="${CSS.escape(d.c)}"]`), texto: valor(`[data-parecer-texto="${CSS.escape(d.c)}"]`) }),
      vista: d => A2('tramVista', { id: aberto, comissao: d.c, pid: valor(`[data-vista="${CSS.escape(d.c)}"]`) }),
      devolverVista: d => A2('tramVistaDevolver', { id: aberto, comissao: d.c }),
      enviarExec: () => A2('tramEnviarExecutivo', { id: aberto, prazoDias: +$('trPrazo').value, oficio: $('trOficio').value }),
      respostaExec: async () => {
        const arq = $('trRespPdf').files[0];
        let dataUrl = '';
        try { dataUrl = await lerPdf(arq); } catch (e) { return PL.avisar(e.message, true); }
        await A2('tramRespostaExecutivo', { id: aberto, texto: $('trResp').value, recebidaEm: $('trRespData').value, nome: arq?.name || '', dataUrl });
        PL.avisar('Resposta registrada.');
      },
    };
    const clique = e => {
      const b = e.target.closest('[data-t]');
      if (!b || b.tagName === 'INPUT' || !acoes[b.dataset.t]) return;
      e.preventDefault();
      Promise.resolve(acoes[b.dataset.t](b.dataset, b)).catch(() => {});
    };
    sec.addEventListener('click', clique);
    $('dlgCorpo').addEventListener('click', clique);
    sec.addEventListener('change', e => {
      const c = e.target;
      if (c.dataset?.t === 'presenca') A2('tramReuniaoPresenca', { id: c.dataset.r, pid: c.dataset.pid, presente: c.checked }).catch(() => { c.checked = !c.checked; });
    });
    if (logado()) carregar();
  }

  /* =================================================================== app do vereador */
  function appParlamentar() {
    const $ = id => document.getElementById(id);
    const seg = document.querySelector('#app .segmento');
    const bt = document.createElement('button');
    bt.dataset.aba = 'protocolo'; bt.setAttribute('role', 'tab');
    bt.innerHTML = 'Protocolo <span id="bolhaRelator"></span>';
    seg.append(bt);
    const caixa = document.createElement('dialog');
    caixa.id = 'dlgTram';
    caixa.innerHTML = '<form method="dialog" class="coluna" style="gap:12px" onsubmit="return false" id="formTram"></form>';
    document.body.append(caixa);
    let M = null, rev = null;
    const token = () => PL.token('parlamentar');

    bt.onclick = () => {
      aba = 'protocolo';  // variável do app do vereador
      document.querySelectorAll('[data-aba]').forEach(x => x.classList.toggle('ativo', x === bt));
      carregar(); scrollTo(0, 0);
    };
    PL.aoMudar(S => { if (eu && S.projetosRev !== rev) { rev = S.projetosRev; carregar(); } });

    async function carregar() {
      if (!token() || typeof eu === 'undefined' || !eu) return;
      const r = await fetch('/api/tramitacao/minhas', { headers: { Authorization: 'Bearer ' + token() } });
      if (!r.ok) return;
      M = await r.json();
      const n = M.relatorias.length + M.minhas.filter(m => m.etapa === 'devolvida').length;
      $('bolhaRelator').textContent = n ? '●' : '';
      $('bolhaRelator').style.color = 'var(--ambar)';
      if (aba === 'protocolo') desenharAba();
    }

    function desenharAba() {
      const ag = M.agora;
      $('conteudoApp').innerHTML = `
        <div class="cartao"><h2>Protocolo eletrônico</h2>
          <p class="detalhe" style="margin-top:0">Projetos, requerimentos, indicações, moções e pedidos de informação, sempre em seu nome. A secretaria confere, numera e devolve o comprovante com o hash do texto.</p>
          <button class="botao principal gr" data-t="nova">Protocolar nova matéria</button></div>
        ${M.relatorias.length ? `<div class="cartao" style="border-top-color:var(--ambar)"><h2>Minhas relatorias</h2>${M.relatorias.map(r => `
          <div style="border-top:1px solid var(--linha);padding:10px 0">
            <div class="grupo" style="justify-content:space-between"><b>${esc(r.numero)}</b>${r.situacao === 'com_relator' ? prazo(r.prazoParecer, ag) : '<span class="chip">Parecer enviado</span>'}</div>
            <p style="margin:4px 0">${esc(r.ementa)}</p><p class="detalhe" style="margin:0">${esc(r.nome)} · autoria: ${esc(r.autor)}</p>
            ${r.texto ? `<details class="detalhe"><summary style="cursor:pointer">Texto da matéria</summary><div style="white-space:pre-wrap;color:var(--texto)">${esc(r.texto)}</div></details>` : ''}
            ${r.parecer ? `<details class="detalhe"><summary style="cursor:pointer">Meu parecer: ${esc(r.parecer.votoNome)}</summary><div style="white-space:pre-wrap;color:var(--texto)">${esc(r.parecer.texto)}</div></details>` : ''}
            <button class="botao pq principal" style="margin-top:8px" data-t="parecer" data-id="${esc(r.materia)}" data-c="${esc(r.comissao)}">${r.parecer ? 'Substituir parecer' : 'Enviar parecer'}</button>
          </div>`).join('')}</div>` : ''}
        ${M.comissoes.length ? `<div class="cartao neutro"><h2>Minhas comissões</h2><p style="margin:0">${M.comissoes.map(c => esc(c.nome) + (c.presidente ? ' (presidente)' : '')).join('<br>')}</p></div>` : ''}
        <div class="cartao neutro"><h2>Minhas matérias</h2>${M.minhas.length ? M.minhas.map(m => `
          <div style="border-top:1px solid var(--linha);padding:10px 0">
            <div class="grupo" style="justify-content:space-between"><b>${esc(m.numero)}</b>${chipEtapa(m)}</div>
            <p style="margin:4px 0">${esc(m.ementa)}</p>
            <p class="detalhe" style="margin:0">${esc(m.tipoNome)}${m.protocolo ? ' · protocolada ' + dataHora(m.protocolo.ts) : ''}${m.comissoes.length ? ' · ' + m.comissoes.map(c => esc(c.nome) + ': ' + esc(c.situacaoNome)).join('; ') : ''}</p>
            ${m.executivo ? `<p class="detalhe" style="margin:4px 0 0">Executivo: ${chipExec(m.executivo)} ${m.executivo.resposta ? '' : prazo(m.executivo.vence, ag)}</p>` : ''}
            ${m.etapa === 'devolvida' ? `<p class="detalhe" style="margin:6px 0;color:var(--vermelho)">Devolvida pela secretaria: ${esc(m.motivo)}</p>
              <button class="botao pq principal" data-t="corrigir" data-id="${esc(m.id)}">Corrigir e reenviar</button>` : ''}
            <div class="grupo" style="margin-top:6px">${m.protocolo ? `<a class="botao pq" href="/api/tramitacao/comprovante/${encodeURIComponent(m.id)}.pdf" target="_blank" rel="noopener">📄 Comprovante</a>
              <a class="botao pq fantasma" href="${linkPublico(m.id)}" target="_blank" rel="noopener">Linha do tempo</a>` : ''}</div>
          </div>`).join('') : '<p class="vazio" style="margin:0">Nenhuma matéria protocolada pelo app ainda.</p>'}</div>`;
    }

    function formulario(m) {
      const f = $('formTram');
      f.innerHTML = `<b style="font-size:18px">${m ? 'Corrigir e reenviar' : 'Protocolar nova matéria'}</b>
        <label class="rot">Tipo<select class="campo" id="tmTipo">${opcoesTipos(M.tipos, m?.tipo || 'PL')}</select></label>
        <label class="rot">Ementa (resumo do que a matéria faz)<textarea class="campo" id="tmEmenta" rows="3" maxlength="${M.limites.ementa}">${esc(m?.ementa || '')}</textarea></label>
        <label class="rot">Texto (artigos, ou o teor do requerimento/indicação)<textarea class="campo" id="tmTexto" rows="8" maxlength="${M.limites.texto}">${esc(m?.texto || '')}</textarea></label>
        <label class="rot">Justificativa<textarea class="campo" id="tmJust" rows="4" maxlength="${M.limites.justificativa}">${esc(m?.justificativa || '')}</textarea></label>
        <small class="suave">Autoria: você. O comprovante traz o hash SHA-256 do texto: qualquer alteração depois do protocolo fica evidente.</small>
        <div class="grupo" style="justify-content:flex-end"><button class="botao fantasma" data-t="fechar">Cancelar</button>
          <button class="botao principal" data-t="enviar" data-id="${esc(m?.id || '')}">Enviar à secretaria</button></div>`;
      caixa.showModal();
    }
    function formParecer(mid, cid) {
      const r = M.relatorias.find(x => x.materia === mid && x.comissao === cid); if (!r) return;
      $('formTram').innerHTML = `<b style="font-size:18px">Parecer — ${esc(r.numero)}</b><span class="detalhe">${esc(r.nome)}</span>
        <label class="rot">Voto do relator<select class="campo" id="tmVoto">${Object.entries(M.votosParecer).map(([k, v]) => `<option value="${k}" ${r.parecer?.voto === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>
        <label class="rot">Parecer (relatório, voto e emendas, se houver)<textarea class="campo" id="tmParecer" rows="10" maxlength="${M.limites.parecer}">${esc(r.parecer?.texto || '')}</textarea></label>
        <div class="grupo" style="justify-content:flex-end"><button class="botao fantasma" data-t="fechar">Cancelar</button>
          <button class="botao principal" data-t="enviarParecer" data-id="${esc(mid)}" data-c="${esc(cid)}">Enviar parecer</button></div>`;
      caixa.showModal();
    }
    const acoes = {
      nova: () => formulario(null),
      corrigir: d => formulario(M.minhas.find(m => m.id === d.id)),
      parecer: d => formParecer(d.id, d.c),
      fechar: () => caixa.close(),
      enviar: async d => {
        await PL.acao('parlamentar', 'protocoloEnviar', { tipo: $('tmTipo').value, ementa: $('tmEmenta').value, texto: $('tmTexto').value,
          justificativa: $('tmJust').value, ...(d.id ? { id: d.id } : {}) });
        caixa.close(); PL.avisar('Enviada à secretaria para protocolo.'); carregar();
      },
      enviarParecer: async d => {
        await PL.acao('parlamentar', 'tramParecer', { id: d.id, comissao: d.c, voto: $('tmVoto').value, texto: $('tmParecer').value });
        caixa.close(); PL.avisar('Parecer enviado.'); carregar();
      },
    };
    const clique = e => {
      const b = e.target.closest('[data-t]');
      if (!b || !acoes[b.dataset.t]) return;
      e.preventDefault();
      Promise.resolve(acoes[b.dataset.t](b.dataset)).catch(() => {});
    };
    $('conteudoApp').addEventListener('click', clique);
    caixa.addEventListener('click', clique);
  }

  if (document.getElementById('aba-sessao') && typeof dialogo === 'function') painelPresidente();
  else if (document.getElementById('conteudoApp')) appParlamentar();
})();
