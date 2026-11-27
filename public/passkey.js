/* Parlatório Legis — passkey / WebAuthn (biometria ou bloqueio de tela do celular) para confirmar voto e presença.
   Incluído depois de comum.js no app do parlamentar e no painel do presidente. O servidor (server/passkey.py) confere tudo;
   aqui só pedimos o desafio, chamamos navigator.credentials e mandamos a resposta junto da ação (campo `passkey`). */
(() => {
  const MARCA = '__passkey__';
  const VIRTUAIS = new Set(['virtualVotar', 'virtualDestaque']);
  const PLENARIO = new Set(['votar', 'presenca', 'presencaRemota']);
  const MODOS = { nao: 'Desligado (só PIN)', opcional: 'Passkey ou PIN', exigir: 'Exigir passkey de quem cadastrou' };
  const pagina = document.getElementById('aba-config') ? 'presidente' : document.getElementById('conteudoApp') ? 'parlamentar' : null;
  if (!pagina) return;
  const pedirPinReal = PL.pedirPin;  // o diálogo de PIN de comum.js (antes do desvio para a passkey)

  const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const deB64u = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0));
  const nome = location.hostname;
  const porIP = /^\d{1,3}(\.\d{1,3}){3}$/.test(nome) || nome.includes(':') || nome.startsWith('[');
  /* WebAuthn: contexto seguro (https:// ou localhost) e rpId = nome do host (IP não serve) */
  function motivoLocal() {
    if (!window.PublicKeyCredential || !navigator.credentials) return 'Este navegador não suporta passkey.';
    if (porIP) return `A passkey não funciona pelo endereço IP. Abra o LEGIS pelo nome, ex.: https://<nome-da-maquina>.local:${location.port || 8443} ou o domínio da Câmara.`;
    if (!window.isSecureContext) return 'A passkey só funciona pelo endereço seguro https:// do LEGIS.';
    return '';
  }

  async function api(caminho, corpo) {
    const r = await fetch(caminho, {
      method: corpo === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (PL.token(pagina) || '') },
      body: corpo === undefined ? undefined : JSON.stringify(corpo)
    });
    const j = await r.json().catch(() => ({ erro: 'Falha de comunicação.' }));
    if (!r.ok) { const e = new Error(j.erro || 'Erro.'); e.codigo = j.codigo; throw e; }
    return j;
  }

  /* ================= parlamentar ================= */
  let info = null;
  const modo = () => PL.estado?.cfg?.passkeyVoto || 'nao';
  const pronta = () => !motivoLocal() && info?.disponivel && info.minhas.length > 0 && modo() !== 'nao';

  async function assercao() {
    const { publicKey: o } = await api('/api/passkey/desafio', { tipo: 'confirmar' });
    const c = await navigator.credentials.get({ publicKey: {
      ...o, challenge: deB64u(o.challenge), allowCredentials: o.allowCredentials.map(x => ({ ...x, id: deB64u(x.id) })) } });
    return { id: c.id, clientDataJSON: b64u(c.response.clientDataJSON), authenticatorData: b64u(c.response.authenticatorData),
             signature: b64u(c.response.signature) };
  }

  async function carregar() {
    if (!PL.token('parlamentar')) return;
    try { info = await api('/api/passkey/estado'); } catch { info = null; }
    desenhar();
  }

  if (pagina === 'parlamentar') {
    /* o diálogo do PIN cede lugar à passkey quando ela vale (o PIN fica de reserva no modo "passkey ou PIN") */
    PL.pedirPin = mensagem => pronta() ? Promise.resolve(MARCA) : pedirPinReal(mensagem);
    const acaoOriginal = PL.acao;
    PL.acao = async (papel, nome, args = {}) => {
      if (papel !== 'parlamentar') return acaoOriginal(papel, nome, args);
      const virtual = VIRTUAIS.has(nome);
      const usar = pronta() && (virtual ? args.pin === MARCA || modo() === 'exigir' : PLENARIO.has(nome) && modo() === 'exigir');
      if (args.pin === MARCA) args = { ...args, pin: undefined };
      if (usar) {
        try {
          args = { ...args, passkey: await assercao() };
          delete args.pin;
        } catch (e) {
          if (virtual && modo() === 'opcional') {   // cancelou a biometria: usa o PIN
            const pin = await pedirPinReal('Passkey não usada. Confirme com seu PIN.');
            if (!pin) throw new Error('Cancelado.');
            args = { ...args, pin };
          } else {
            PL.avisar(e.codigo ? e.message : 'Confirmação pela passkey cancelada.', true);
            throw e;
          }
        }
      }
      if (args.pin === undefined) delete args.pin;
      return acaoOriginal(papel, nome, args);
    };
  }

  async function cadastrar() {
    try {
      const pin = await pedirPinReal('Para cadastrar a passkey deste celular, confirme seu PIN.');
      if (!pin) return;
      const apelido = (prompt('Nome deste aparelho (ex.: Meu celular):', 'Meu celular') || '').trim() || 'Meu celular';
      const { publicKey: o } = await api('/api/passkey/desafio', { tipo: 'cadastro' });
      const c = await navigator.credentials.create({ publicKey: {
        ...o, challenge: deB64u(o.challenge), user: { ...o.user, id: deB64u(o.user.id) },
        excludeCredentials: o.excludeCredentials.map(x => ({ ...x, id: deB64u(x.id) })) } });
      await api('/api/passkey/cadastrar', { pin, nome: apelido, credencial: {
        id: c.id, clientDataJSON: b64u(c.response.clientDataJSON), attestationObject: b64u(c.response.attestationObject) } });
      PL.avisar('Passkey cadastrada.');
    } catch (e) {
      if (e.codigo === 'pin') PL.esquecerPin();
      PL.avisar(e.name === 'NotAllowedError' ? 'Cadastro cancelado.' : e.name === 'InvalidStateError' ? 'Este celular já tem passkey cadastrada.' : e.message, true);
    }
    carregar();
  }

  async function remover(id) {
    if (!confirm('Remover esta passkey? Você volta a confirmar com o PIN.')) return;
    const pin = await pedirPinReal('Para remover a passkey, confirme seu PIN.');
    if (!pin) return;
    try {
      await api('/api/passkey/remover', { id, pin });
      PL.avisar('Passkey removida.');
    } catch (e) { if (e.codigo === 'pin') PL.esquecerPin(); PL.avisar(e.message, true); }
    carregar();
  }

  function desenhar() {
    if (pagina !== 'parlamentar') return;
    let el = document.getElementById('cartaoPasskey');
    if (!el) {
      el = document.createElement('details');
      el.id = 'cartaoPasskey'; el.className = 'cartao neutro'; el.style.marginTop = '12px';
      (document.getElementById('cartaoRegimento') || document.getElementById('conteudoApp')).after(el);
    }
    const aberto = el.open, esc = PL.esc, m = modo();
    const motivo = motivoLocal() || (info && !info.disponivel ? info.motivo : '');
    const lista = (info?.minhas || []).map(p => `<li style="display:flex;gap:8px;align-items:center;justify-content:space-between;margin:6px 0">
      <span><b>${esc(p.nome)}</b> <span class="detalhe">${p.usado ? 'usada ' + new Date(p.usado).toLocaleString('pt-BR') : 'ainda não usada'}</span></span>
      <button class="botao pq fantasma" data-remover="${esc(p.id)}">Remover</button></li>`).join('');
    el.innerHTML = `<summary style="cursor:pointer;font-weight:700">🔐 Passkey (biometria do celular)</summary>
      <p class="detalhe">Confirme voto e presença com a digital, o rosto ou o bloqueio de tela deste aparelho, sem digitar o PIN.
      ${m === 'exigir' ? 'A Câmara <b>exige</b> passkey de quem cadastrou.' : m === 'opcional' ? 'A Câmara aceita passkey ou PIN.' : 'A Câmara ainda não ativou a passkey no voto (cadastrar já é possível).'}</p>
      ${motivo ? `<p class="detalhe" style="color:var(--ambar)">${esc(motivo)}</p>` : `<button class="botao principal pq" id="pkCadastrar">Cadastrar este celular</button>`}
      ${lista ? `<ul style="list-style:none;padding:0;margin:10px 0 0">${lista}</ul>` : '<p class="detalhe">Nenhuma passkey cadastrada.</p>'}
      <p class="detalhe">Perdeu o celular? Peça à presidência para revogar suas passkeys.</p>`;
    el.open = aberto;
    el.querySelector('#pkCadastrar')?.addEventListener('click', cadastrar);
    el.querySelectorAll('[data-remover]').forEach(b => b.addEventListener('click', () => remover(b.dataset.remover)));
  }

  /* ================= presidente ================= */
  let contas = {};
  async function carregarPresidente() {
    if (!PL.token('presidente') || !PL.estado) return;
    try { contas = (await api('/api/passkey/estado')).contas || {}; } catch { return; }
    desenharPresidente();
  }
  function desenharPresidente() {
    const destino = document.querySelector('#aba-config .grade2 .coluna');
    if (!destino || !PL.estado) return;
    let el = document.getElementById('cartaoPasskeyPres');
    if (!el) { el = document.createElement('div'); el.id = 'cartaoPasskeyPres'; el.className = 'cartao'; destino.append(el); }
    const esc = PL.esc, m = modo();
    const linhas = PL.estado.parls.filter(p => contas[p.id]).map(p => `<li style="display:flex;gap:8px;align-items:center;justify-content:space-between;margin:4px 0">
      <span>${esc(p.nome)} <span class="detalhe">(${contas[p.id]} passkey${contas[p.id] > 1 ? 's' : ''})</span></span>
      <button class="botao pq fantasma" data-revogar="${esc(p.id)}">Revogar todas</button></li>`).join('');
    el.innerHTML = `<h2>Passkey no voto (biometria)</h2>
      <label class="linha">Confirmação do voto e da presença pelo celular
        <select class="campo" id="pkModo" style="width:auto">${Object.entries(MODOS).map(([k, v]) => `<option value="${k}" ${k === m ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <p class="detalhe">Vale para quem cadastrou passkey no próprio celular. Quem não cadastrou continua com o PIN. A passkey só funciona pelo endereço com nome (https://…), nunca pelo IP.</p>
      ${linhas ? `<ul style="list-style:none;padding:0;margin:8px 0 0">${linhas}</ul>` : '<p class="detalhe">Nenhum vereador cadastrou passkey ainda.</p>'}`;
    el.querySelector('#pkModo').onchange = async e => {
      try { await api('/api/passkey/modo', { modo: e.target.value }); PL.avisar('Opção da passkey salva.'); } catch (x) { PL.avisar(x.message, true); }
    };
    el.querySelectorAll('[data-revogar]').forEach(b => b.onclick = async () => {
      if (!confirm(`Revogar todas as passkeys de ${PL.parl(b.dataset.revogar).nome}? (ex.: celular perdido)`)) return;
      try { await api('/api/passkey/revogar', { pid: b.dataset.revogar }); PL.avisar('Passkeys revogadas.'); } catch (x) { PL.avisar(x.message, true); }
      carregarPresidente();
    });
  }

  let primeira = true, modoVisto = null;
  PL.aoMudar(() => {
    if (pagina === 'presidente') {
      if (primeira) { primeira = false; carregarPresidente(); }
      else if (modoVisto !== modo() && document.getElementById('cartaoPasskeyPres')) desenharPresidente();
    } else if (primeira) { primeira = false; carregar(); }
    else if (modoVisto !== modo()) desenhar();
    modoVisto = modo();
  });
  if (pagina === 'presidente') document.querySelector('[data-aba="config"]')?.addEventListener('click', carregarPresidente);
})();
