/* Parlatório Legis — funções comuns às três telas (presidente, telão, parlamentar). */
const PL = (() => {
  let estado = null;
  let desvio = 0;            // relógio do servidor − relógio local (ms)
  let ultimoSinal = null;
  const ouvintes = [];
  const tokenChave = papel => 'parlatorio.token.' + papel;

  /* ----- conexão (SSE) ----- */
  function conectar() {
    const fonte = new EventSource('/eventos');
    const bolinha = () => document.querySelectorAll('.conexao');
    fonte.onopen = () => bolinha().forEach(b => { b.className = 'conexao on'; b.title = 'Conectado'; });
    fonte.onerror = () => bolinha().forEach(b => { b.className = 'conexao off'; b.title = 'Sem conexão — tentando novamente'; });
    fonte.onmessage = e => {
      const m = JSON.parse(e.data);
      desvio = m.agora - Date.now();
      if (m.tipo !== 'estado') return;
      const primeira = !estado;
      estado = m.estado;
      aplicarTema();
      if (!primeira && ultimoSinal !== null && estado.sinal.n !== ultimoSinal) PL.aoSinal?.(estado.sinal.tipo);
      ultimoSinal = estado.sinal.n;
      ouvintes.forEach(f => f(estado));
    };
  }
  const agora = () => Date.now() + desvio;

  /* ----- API ----- */
  async function entrar(papel, pin, pid) {
    const r = await fetch('/api/entrar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ papel, pin, pid }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro);
    localStorage.setItem(tokenChave(papel), j.token);
    return j.ator;
  }
  async function quemSou(papel) {
    const t = localStorage.getItem(tokenChave(papel));
    if (!t) return null;
    const r = await fetch('/api/eu', { headers: { Authorization: 'Bearer ' + t } });
    const j = await r.json();
    return j.ator && j.ator.papel === papel ? j.ator : null;
  }
  async function sair(papel) {
    const t = localStorage.getItem(tokenChave(papel));
    if (t) await fetch('/api/sair', { method: 'POST', headers: { Authorization: 'Bearer ' + t } }).catch(() => {});
    localStorage.removeItem(tokenChave(papel));
  }
  async function acao(papel, nome, args = {}) {
    const r = await fetch('/api/acao', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem(tokenChave(papel)) },
      body: JSON.stringify({ nome, args })
    });
    const j = await r.json().catch(() => ({ erro: 'Falha de comunicação.' }));
    if (r.status === 401) { localStorage.removeItem(tokenChave(papel)); location.reload(); }
    if (j.codigo === 'trocarPin') { exigirTrocaPin(papel); throw new Error(j.erro); }
    if (!r.ok) { avisar(j.erro, true); const e = new Error(j.erro); e.codigo = j.codigo; throw e; }
    return j;
  }

  /* troca de PIN: obrigatória enquanto o PIN for o padrão (0000, 1234…) */
  async function trocarPin(papel, atual, novo) {
    const r = await fetch('/api/trocar-pin', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem(tokenChave(papel)) },
      body: JSON.stringify({ atual, novo }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.erro || 'Não foi possível trocar o PIN.');
  }
  function exigirTrocaPin(papel, opcional = false) {
    if (document.getElementById('dlgPin')) return;
    const minimo = papel === 'presidente' ? 6 : 4;
    const d = document.createElement('dialog');
    d.id = 'dlgPin';
    d.style.cssText = 'background:var(--cartao);color:var(--texto);border:0;border-top:4px solid var(--destaque-suave);border-radius:14px;padding:18px;width:min(400px,92vw)';
    d.innerHTML = `<form method="dialog" style="display:flex;flex-direction:column;gap:12px">
      <b style="font-size:18px">${opcional ? 'Trocar meu PIN' : 'Crie seu PIN pessoal'}</b>
      ${opcional ? '' : '<p style="margin:0;color:var(--suave);font-size:14px">Você entrou com o PIN padrão. Por segurança, crie um PIN só seu antes de continuar.</p>'}
      <label class="rot">PIN atual<input class="campo" type="password" inputmode="numeric" autocomplete="current-password" id="pinAtual"></label>
      <label class="rot">Novo PIN (${minimo} a 8 números, sem sequências como 1234)<input class="campo" type="password" inputmode="numeric" autocomplete="new-password" id="pinNovo"></label>
      <label class="rot">Repita o novo PIN<input class="campo" type="password" inputmode="numeric" autocomplete="new-password" id="pinNovo2"></label>
      <div id="pinErro" style="color:var(--vermelho);font-size:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        ${opcional ? '<button class="botao fantasma" value="cancelar" formnovalidate>Cancelar</button>' : `<button class="botao fantasma" type="button" id="pinSair">Sair</button>`}
        <button class="botao principal" id="pinOk" type="button">Salvar PIN</button></div></form>`;
    document.body.append(d);
    d.addEventListener('cancel', e => { if (!opcional) e.preventDefault(); });
    d.addEventListener('close', () => d.remove());
    d.querySelector('#pinSair')?.addEventListener('click', () => sair(papel).then(() => location.reload()));
    d.querySelector('#pinOk').onclick = async () => {
      const novo = d.querySelector('#pinNovo').value;
      if (novo !== d.querySelector('#pinNovo2').value) { d.querySelector('#pinErro').textContent = 'Os dois PINs novos não conferem.'; return; }
      try { await trocarPin(papel, d.querySelector('#pinAtual').value, novo); d.close(); avisar('PIN trocado.'); if (!opcional) location.reload(); }
      catch (e) { d.querySelector('#pinErro').textContent = e.message; }
    };
    d.showModal();
  }

  /* confirmação do PIN para votar online; fica guardado só nesta página por 10 minutos */
  let pinCache = null;
  function pedirPin(mensagem) {
    if (pinCache && Date.now() - pinCache.ts < 600000) return Promise.resolve(pinCache.pin);
    return new Promise(ok => {
      const d = document.createElement('dialog');
      d.style.cssText = 'background:var(--cartao);color:var(--texto);border:0;border-top:4px solid var(--ambar);border-radius:14px;padding:18px;width:min(360px,92vw)';
      d.innerHTML = `<form method="dialog" style="display:flex;flex-direction:column;gap:12px">
        <b>Confirme com seu PIN</b><span style="color:var(--suave);font-size:14px">${esc(mensagem)}</span>
        <input class="campo" type="password" inputmode="numeric" autocomplete="current-password" style="font-size:24px;letter-spacing:.3em;text-align:center" id="pinConf">
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;color:var(--suave)"><input type="checkbox" id="pinLembrar" checked> não pedir de novo por 10 minutos</label>
        <div style="display:flex;gap:8px;justify-content:flex-end"><button class="botao fantasma" value="">Cancelar</button><button class="botao principal" value="ok">Confirmar</button></div></form>`;
      document.body.append(d);
      d.addEventListener('close', () => {
        const pin = d.returnValue === 'ok' ? d.querySelector('#pinConf').value : null;
        if (pin && d.querySelector('#pinLembrar').checked) pinCache = { pin, ts: Date.now() };
        d.remove(); ok(pin);
      });
      d.showModal();
      setTimeout(() => d.querySelector('#pinConf').focus(), 50);
    });
  }
  const esquecerPin = () => { pinCache = null; };

  /* ----- formatação ----- */
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function tempo(ms) {
    const neg = ms < 0; ms = Math.abs(ms);
    const s = neg ? Math.floor(ms / 1000) : Math.ceil(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60;
    return (neg ? '+' : '') + (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(ss).padStart(2, '0');
  }
  const hora = ts => new Date(ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const relogio = () => new Date(agora()).toLocaleTimeString('pt-BR');
  const decorrido = t => t.acc + (t.start ? agora() - t.start : 0);
  const restante = t => t.total - decorrido(t);
  function nivel(t) {
    const r = restante(t);
    return r <= 0 ? "fim" : r <= Math.min(estado.cfg.alerta * 1000, t.total / 4) ? "alerta" : "ok";
  }
  function pintar(t, elTempo, elBarra) {
    if (!elTempo) return;
    const r = restante(t), n = nivel(t);
    elTempo.textContent = tempo(r);
    elTempo.classList.remove('n-ok', 'n-alerta', 'n-fim', 'pisca');
    elTempo.classList.add('n-' + n);
    if (n === 'fim') elTempo.classList.add('pisca');
    if (elBarra) {
      elBarra.className = 'barra n-' + n;
      elBarra.firstElementChild.style.width = Math.max(0, Math.min(100, r / t.total * 100)) + '%';
    }
  }

  /* ----- parlamentares ----- */
  const parl = id => estado.parls.find(p => p.id === id) || { id, nome: '(removido)', partido: '', fotoV: 0 };
  const fase = id => estado.fases.find(f => f.id === id) || { nome: '—', min: 0, totalMin: 0 };
  const corPartido = sig => estado.partidos[sig] || '#5b6b86';
  function iniciais(n) { const p = n.split(' ').filter(w => w.length > 2); return ((p[0] || n)[0] + ((p.at(-1) || '')[0] || '')).toUpperCase(); }
  function avatar(p, classe = '', estilo = '') {
    const conteudo = p.fotoV ? `<img src="/imagem/${p.id}?v=${p.fotoV}" alt="">` : esc(iniciais(p.nome));
    return `<div class="av ${classe}" style="--cor-partido:${corPartido(p.partido)};${p.fotoV ? '' : 'background:' + corPartido(p.partido) + ';'}${estilo}">${conteudo}</div>`;
  }
  const sigla = s => s ? `<span class="sigla" style="--cor-partido:${corPartido(s)}">${esc(s)}</span>` : '';
  const TIPOS = { aparte: 'Aparte', ordem: 'Questão de ordem', pelaordem: 'Pela ordem', inscricao: 'Inscrição' };
  const SITUACOES = { pendente: 'Pendente', em_discussao: 'Em discussão', em_votacao: 'Em votação', aprovada: 'Aprovada', rejeitada: 'Rejeitada', adiada: 'Adiada', retirada: 'Retirada', sem_quorum: 'Sem quórum' };
  const QUORUNS = { simples: 'Maioria simples', absoluta: 'Maioria absoluta', dois_tercos: 'Dois terços' };
  const VOTOS = { sim: 'Sim', nao: 'Não', abst: 'Abstenção' };
  const MICS = { aberto: 'Microfone aberto', fechado: 'Microfone fechado', cortado: 'Microfone cortado', tribuna: 'Na tribuna' };
  const OCORRENCIAS = { congelar: 'Tempo congelado', descongelar: 'Tempo descongelado', corte: 'Microfone cortado', liberacao: 'Microfone liberado',
    tempo_esgotado: 'Tempo esgotado', plenario_congelado: 'Plenário congelado', plenario_retomado: 'Sessão retomada',
    encerramento_antecipado: 'Encerramento antecipado' };
  const mic = pid => estado.microfones?.[pid] || 'fechado';
  function micChip(pid) {
    const m = mic(pid);
    const cls = { aberto: 'vivo', tribuna: 'vivo', cortado: 'corte', fechado: '' }[m];
    return `<span class="chip ${cls}" title="${MICS[m]}"><span class="ponto"></span>${m === 'cortado' ? 'Cortado' : m === 'fechado' ? 'Mic fechado' : 'Mic aberto'}</span>`;
  }
  const gravando = () => (estado.gravacoes || []).some(g => !g.fim && agora() - g.ultimo < 20000);
  function quorum() {
    const n = estado.parls.length, presentes = Object.keys(estado.presenca).length, minimo = Math.floor(n / 2) + 1;
    return { n, presentes, minimo, ok: presentes >= minimo };
  }
  function usadoNaFase(fid) {
    let s = estado.hist.filter(h => h.fase === fid).reduce((a, h) => a + h.usado * 1000, 0);
    if (estado.orador?.fase === fid) s += decorrido(estado.orador);
    return s;
  }

  /* ----- tema ----- */
  function aplicarTema() {
    const c = estado.casa, raiz = document.documentElement;
    raiz.style.setProperty('--destaque', c.destaque || '#1f8a4c');
    if (!raiz.dataset.temaFixo) raiz.dataset.tema = c.tema === 'claro' ? 'claro' : 'escuro';
    document.querySelectorAll('[data-logo]').forEach(img => { img.src = c.logoV ? `/imagem/logo?v=${c.logoV}` : '/marca/legis-icone.png'; });
    document.querySelectorAll('[data-casa]').forEach(el => { el.textContent = c.nome; });
    document.querySelectorAll('[data-sessao]').forEach(el => { el.textContent = c.titulo; });
  }

  /* ----- som e avisos ----- */
  let actx;
  function bipe(tipo) {
    try {
      actx ||= new AudioContext();
      const [freq, n] = tipo === 'fim' ? [440, 3] : [660, 1];
      for (let i = 0; i < n; i++) {
        const o = actx.createOscillator(), g = actx.createGain(), t0 = actx.currentTime + i * .4;
        o.frequency.value = freq; o.connect(g); g.connect(actx.destination);
        g.gain.setValueAtTime(.25, t0); g.gain.exponentialRampToValueAtTime(.001, t0 + .3);
        o.start(t0); o.stop(t0 + .3);
      }
    } catch {}
  }
  function avisar(texto, erro = false) {
    let box = document.getElementById('avisos');
    if (!box) { box = document.createElement('div'); box.id = 'avisos'; document.body.append(box); }
    const el = document.createElement('div');
    el.className = 'aviso' + (erro ? ' erro' : ''); el.textContent = texto;
    box.append(el); setTimeout(() => el.remove(), 3500);
  }

  /* videoconferência (Jitsi Meet). A sala só vem do servidor para quem está logado. */
  let jitsi = null;
  async function video(container, papel) {
    const r = await fetch('/api/remoto', { headers: { Authorization: 'Bearer ' + localStorage.getItem(tokenChave(papel)) } });
    const cfg = await r.json();
    if (!r.ok) throw new Error(cfg.erro || 'Sessão remota indisponível.');
    if (!window.JitsiMeetExternalAPI) {
      await new Promise((ok, falha) => {
        const sc = document.createElement('script');
        sc.src = cfg.servidor + '/external_api.js'; sc.onload = ok; sc.onerror = () => falha(new Error('Não foi possível carregar o Jitsi.'));
        document.head.append(sc);
      });
    }
    jitsi?.dispose();
    jitsi = new JitsiMeetExternalAPI(new URL(cfg.servidor).host, {
      roomName: cfg.sala, parentNode: container, width: '100%', height: '100%', ...(cfg.jwt ? { jwt: cfg.jwt } : {}),
      userInfo: { displayName: cfg.nome },
      configOverwrite: { startWithAudioMuted: true, prejoinPageEnabled: false, disableDeepLinking: true },
      interfaceConfigOverwrite: { MOBILE_APP_PROMO: false }
    });
    return jitsi;
  }
  /* mantém o microfone da videoconferência igual ao que o plenário decidiu (aberto/fechado/cortado) */
  async function sincronizarMicrofone(aberto) {
    if (!jitsi) return;
    const mudo = await jitsi.isAudioMuted();
    if (aberto === mudo) jitsi.executeCommand('toggleAudio');
  }
  const encerrarVideo = () => { jitsi?.dispose(); jitsi = null; };

  /* reduz uma foto para um quadrado de 320 px em JPEG antes de enviar */
  function lerImagem(arquivo, lado = 320, quadrado = true, formato = quadrado ? 'jpeg' : 'png') {
    return new Promise((ok, falha) => {
      const img = new Image();
      img.onload = () => {
        const c = document.createElement('canvas');
        let sx = 0, sy = 0, sw = img.width, sh = img.height;
        if (quadrado) { const m = Math.min(sw, sh); sx = (sw - m) / 2; sy = (sh - m) / 2; sw = sh = m; c.width = c.height = lado; }
        else { const k = Math.min(1, lado / Math.max(sw, sh)); c.width = sw * k; c.height = sh * k; }
        const g = c.getContext('2d');
        if (formato === 'jpeg') { g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); }  // JPEG não tem transparência
        g.drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
        ok(c.toDataURL('image/' + formato, .88));
        URL.revokeObjectURL(img.src);
      };
      img.onerror = () => falha(new Error('Imagem inválida.'));
      img.src = URL.createObjectURL(arquivo);
    });
  }

  return {
    conectar, agora, entrar, quemSou, sair, acao, esc, tempo, hora, relogio, decorrido, restante, nivel, pintar,
    parl, fase, corPartido, avatar, sigla, TIPOS, SITUACOES, QUORUNS, VOTOS, quorum, usadoNaFase, bipe, avisar, lerImagem,
    MICS, OCORRENCIAS, mic, micChip, gravando, token: papel => localStorage.getItem(tokenChave(papel)),
    video, sincronizarMicrofone, encerrarVideo, temVideo: () => !!jitsi,
    exigirTrocaPin, pedirPin, esquecerPin,
    get estado() { return estado; },
    aoMudar: f => ouvintes.push(f),
    aoSinal: null
  };
})();

/* Logotipo do LEGIS no canto superior esquerdo do menu de cada página */
document.querySelectorAll('.nav .menu').forEach(m => {
  if (m.querySelector('.selo-nav')) return;
  const a = document.createElement('a');
  a.className = 'selo-nav'; a.href = '/'; a.title = 'LEGIS — Sistema de Gestão Parlamentar';
  a.innerHTML = '<img src="/marca/legis-claro.webp" alt="LEGIS">';
  m.prepend(a);
});
