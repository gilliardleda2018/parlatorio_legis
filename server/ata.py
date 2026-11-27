"""Ata da sessão em PDF, sem dependências externas.

Um gerador de PDF mínimo (A4, Helvetica, tabelas, imagem JPEG) e a montagem
da ata a partir do retrato da sessão: presença, pauta, pronunciamentos,
votações (com o voto nominal de cada parlamentar), ocorrências e gravações.
"""
import time
import unicodedata

# larguras (1/1000 em) das fontes padrão do PDF, caracteres 32..126
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
         556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
         722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
         556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
         260, 334, 584]
_HELV_B = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
           556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
           722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611,
           556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389,
           280, 389, 584]
_ESPECIAIS = {"—": 1000, "–": 556, "“": 333, "”": 333, "‘": 222, "’": 222, "•": 350, "º": 365, "ª": 370, "…": 1000, "·": 278}

A4 = (595.28, 841.89)
CINZA = (0.42, 0.47, 0.55)
DESTAQUE = (0.12, 0.54, 0.30)  # verde #1f8a4c, cor padrão das casas
ESCURO = (0.08, 0.12, 0.2)
LINHA = (0.82, 0.85, 0.9)
ZEBRA = (0.955, 0.965, 0.98)


def _larg_char(c: str, negrito: bool) -> int:
    if c in _ESPECIAIS:
        return _ESPECIAIS[c]
    base = unicodedata.normalize("NFD", c)[0] if c else " "
    o = ord(base)
    tabela = _HELV_B if negrito else _HELV
    return tabela[o - 32] if 32 <= o <= 126 else 556


def largura(s: str, tam: float, negrito=False) -> float:
    return sum(_larg_char(c, negrito) for c in s) * tam / 1000


def _pdf_str(s: str) -> str:
    b = s.encode("cp1252", errors="replace")
    return "(" + "".join("\\" + chr(x) if x in (40, 41, 92) else chr(x) for x in b) + ")"


def _cor(c) -> str:
    return f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f}"


def quebrar(s: str, maximo: float, tam: float, negrito=False) -> list[str]:
    linhas = []
    for paragrafo in str(s).split("\n"):
        atual = ""
        for palavra in paragrafo.split(" "):
            teste = (atual + " " + palavra).strip()
            if largura(teste, tam, negrito) <= maximo or not atual:
                while largura(teste, tam, negrito) > maximo and len(teste) > 1:  # palavra maior que a coluna
                    corte = len(teste)
                    while corte > 1 and largura(teste[:corte], tam, negrito) > maximo:
                        corte -= 1
                    linhas.append(teste[:corte])
                    teste = teste[corte:]
                atual = teste
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
    return linhas


class PDF:
    def __init__(self, titulo_rodape: str = ""):
        self.cor = DESTAQUE
        self.L, self.A = A4
        self.m = 48
        self.paginas: list[list[str]] = []
        self.imagens: list[tuple[bytes, int, int, int]] = []
        self.rodape = titulo_rodape
        self.nova_pagina()

    # ---- primitivas
    def nova_pagina(self):
        self.paginas.append([])
        self.y = self.A - self.m

    @property
    def ops(self):
        return self.paginas[-1]

    def texto(self, x, y, s, tam=10, negrito=False, cor=ESCURO):
        self.ops.append(f"BT /{'F2' if negrito else 'F1'} {tam:.2f} Tf {_cor(cor)} rg {x:.2f} {y:.2f} Td {_pdf_str(s)} Tj ET")

    def retangulo(self, x, y, w, h, cor):
        self.ops.append(f"{_cor(cor)} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def linha(self, x1, y1, x2, y2, cor=LINHA, esp=0.6):
        self.ops.append(f"{_cor(cor)} RG {esp:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def jpeg(self, dados: bytes, x, y, w, h):
        larg, alt, comp = _dim_jpeg(dados)
        if not larg:
            return
        self.imagens.append((dados, larg, alt, comp))
        self.ops.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /Im{len(self.imagens)} Do Q")

    # ---- fluxo
    def garantir(self, altura):
        if self.y - altura < self.m + 24:
            self.nova_pagina()

    def espaco(self, h):
        self.y -= h

    def paragrafo(self, s, tam=10, negrito=False, cor=ESCURO, recuo=0, entrelinha=1.35):
        for linha in quebrar(s, self.L - 2 * self.m - recuo, tam, negrito):
            self.garantir(tam * entrelinha)
            self.y -= tam * entrelinha
            self.texto(self.m + recuo, self.y, linha, tam, negrito, cor)

    def secao(self, numero, titulo):
        self.garantir(130)  # não deixa o título sozinho no pé da página
        self.espaco(14)
        self.texto(self.m, self.y - 12, f"{numero}. {titulo.upper()}", 11, True, ESCURO)
        self.y -= 18
        self.linha(self.m, self.y, self.L - self.m, self.y, self.cor, 1.2)
        self.espaco(6)

    def tabela(self, colunas, linhas, tam=8.5):
        """colunas: [(título, fração da largura, 'e'|'d'|'c')]; linhas: listas de textos."""
        total = self.L - 2 * self.m
        larg = [c[1] * total for c in colunas]
        pad = 4

        def cabecalho():
            h = tam + 9
            self.garantir(h + tam * 2)
            self.retangulo(self.m, self.y - h, total, h, (0.11, 0.16, 0.27))
            x = self.m
            for (tit, _, _), w in zip(colunas, larg):
                self.texto(x + pad, self.y - h + 6, tit, tam - 0.5, True, (1, 1, 1))
                x += w
            self.y -= h

        cabecalho()
        for k, lin in enumerate(linhas):
            celulas = [quebrar(str(v), w - 2 * pad, tam) for v, w in zip(lin, larg)]
            h = max(len(c) for c in celulas) * tam * 1.25 + 6
            if self.y - h < self.m + 24:
                self.nova_pagina()
                cabecalho()
            if k % 2:
                self.retangulo(self.m, self.y - h, total, h, ZEBRA)
            x = self.m
            for linhas_cel, w, (_, _, alin) in zip(celulas, larg, colunas):
                yy = self.y - tam - 2
                for t in linhas_cel:
                    tx = x + pad if alin == "e" else x + w - pad - largura(t, tam) if alin == "d" else x + (w - largura(t, tam)) / 2
                    self.texto(tx, yy, t, tam)
                    yy -= tam * 1.25
                x += w
            self.y -= h
            self.linha(self.m, self.y, self.m + total, self.y)

    # ---- saída
    def gerar(self) -> bytes:
        n = len(self.paginas)
        for i, ops in enumerate(self.paginas, 1):
            self.paginas[i - 1] = ops + [
                f"{_cor(LINHA)} RG 0.6 w {self.m:.2f} 34 m {self.L - self.m:.2f} 34 l S",
                f"BT /F1 7.5 Tf {_cor(CINZA)} rg {self.m:.2f} 22 Td {_pdf_str(self.rodape)} Tj ET",
                f"BT /F1 7.5 Tf {_cor(CINZA)} rg {self.L - self.m - largura(f'Página {i} de {n}', 7.5):.2f} 22 Td {_pdf_str(f'Página {i} de {n}')} Tj ET",
            ]
        objs: list[bytes] = []

        def add(b) -> int:
            objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
            return len(objs)

        f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        imgs = []
        for dados, w, h, comp in self.imagens:
            esp = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comp, "/DeviceRGB")
            imgs.append(add(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {esp} /BitsPerComponent 8 "
                            f"/Filter /DCTDecode /Length {len(dados)} >>\nstream\n".encode() + dados + b"\nendstream"))
        recursos = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << " + \
                   " ".join(f"/Im{k} {o} 0 R" for k, o in enumerate(imgs, 1)) + " >> >>"
        paginas_id = len(objs) + 1 + 2 * n  # reservado depois das páginas
        kids = []
        for ops in self.paginas:
            conteudo = "\n".join(ops).encode("latin-1")
            c = add(f"<< /Length {len(conteudo)} >>\nstream\n".encode() + conteudo + b"\nendstream")
            kids.append(add(f"<< /Type /Page /Parent {paginas_id} 0 R /MediaBox [0 0 {self.L} {self.A}] "
                            f"/Resources {recursos} /Contents {c} 0 R >>"))
        assert add(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {n} >>") == paginas_id
        catalogo = add(f"<< /Type /Catalog /Pages {paginas_id} 0 R >>")
        saida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        posicoes = []
        for i, o in enumerate(objs, 1):
            posicoes.append(len(saida))
            saida += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(saida)
        saida += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        saida += "".join(f"{p:010d} 00000 n \n" for p in posicoes).encode()
        saida += f"trailer\n<< /Size {len(objs) + 1} /Root {catalogo} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(saida)


def _dim_jpeg(d: bytes) -> tuple[int, int, int]:
    if d[:2] != b"\xff\xd8":
        return 0, 0, 0
    i = 2
    while i < len(d) - 9:
        if d[i] != 0xFF:
            i += 1
            continue
        marca = d[i + 1]
        if marca in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(d[i + 7:i + 9], "big"), int.from_bytes(d[i + 5:i + 7], "big"), d[i + 9]
        i += 2 + int.from_bytes(d[i + 2:i + 4], "big")
    return 0, 0, 0


# ------------------------------------------------------------------ ata
TIPOS = {"aparte": "Aparte", "ordem": "Questão de ordem", "pelaordem": "Pela ordem"}
SECOES_PAUTA = {"expediente": "Expediente", "ordem_do_dia": "Ordem do Dia"}
SITUACOES = {"pendente": "Pendente", "em_discussao": "Em discussão", "em_votacao": "Em votação", "aprovada": "Aprovada",
             "rejeitada": "Rejeitada", "adiada": "Adiada", "retirada": "Retirada", "sem_quorum": "Sem quórum"}
QUORUNS = {"simples": "maioria simples", "absoluta": "maioria absoluta", "dois_tercos": "dois terços"}
PROCESSOS = {"nominal": "nominal", "simbolica": "simbólica", "secreta": "secreta"}
VOTOS = {"sim": "Sim", "nao": "Não", "abst": "Abstenção"}
OCORRENCIAS = {"congelar": "Tempo congelado", "descongelar": "Tempo descongelado", "corte": "Microfone cortado",
               "liberacao": "Microfone liberado", "tempo_esgotado": "Tempo esgotado", "plenario_congelado": "Plenário congelado",
               "plenario_retomado": "Sessão retomada", "encerramento_antecipado": "Encerramento antecipado"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _hora(ms):
    return time.strftime("%H:%M", time.localtime(ms / 1000)) if ms else "—"


def _data_extenso(ms):
    t = time.localtime(ms / 1000)
    return f"{t.tm_mday} de {MESES[t.tm_mon - 1]} de {t.tm_year}"


def _dur(seg):
    seg = int(max(0, seg))
    h, m, s = seg // 3600, seg % 3600 // 60, seg % 60
    return f"{h}h{m:02d}min" if h else f"{m:02d}:{s:02d}"


def _rgb(hexa: str) -> tuple[float, float, float]:
    h = (hexa or "").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) if len(h) == 6 else DESTAQUE
    except ValueError:
        return DESTAQUE


def gerar_ata(r: dict, logo_jpeg: bytes | None = None, gerado_em: int | None = None, cor: str = "") -> bytes:
    destaque = _rgb(cor)
    gerado_em = gerado_em or int(time.time() * 1000)
    parls = {p["id"]: p for p in r["parls"]}
    nome = lambda pid: parls.get(pid, {}).get("nome", "(removido)")
    materias = {m["id"]: m for m in r["pauta"]}
    for v in r["votacoes"]:
        materias.setdefault(v["materia"], {"numero": v["numero"], "ementa": v["ementa"]})
    pdf = PDF(f"{r['casa']} · {r['titulo']} · gerada pelo Parlatório Legis em "
              f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(gerado_em / 1000))}")
    pdf.cor = destaque

    # cabeçalho
    topo = pdf.y
    x_txt = pdf.m
    if logo_jpeg:
        w, h, _ = _dim_jpeg(logo_jpeg)
        if w:
            alt = 58
            pdf.jpeg(logo_jpeg, pdf.m, topo - alt, alt * w / h, alt)
            x_txt = pdf.m + alt * w / h + 14
    pdf.texto(x_txt, topo - 16, r["casa"].upper(), 13, True)
    pdf.texto(x_txt, topo - 34, f"ATA DA {r['titulo'].upper()}", 11.5, True, destaque)
    fim = r.get("fim")
    pdf.texto(x_txt, topo - 50, (f"Sessão virtual · votação de {time.strftime('%d/%m/%Y %H:%M', time.localtime(r['inicio'] / 1000))} "
              f"a {time.strftime('%d/%m/%Y %H:%M', time.localtime(fim / 1000))}" if r.get("virtual") else
              f"{_data_extenso(r['inicio'])}" + ("" if r.get("semHorario") else f" · de {_hora(r['inicio'])} às {_hora(fim) if fim else '—'}"))
              + ("  ·  PRÉVIA (sessão em andamento)" if not fim else ""), 9, False, CINZA)
    pdf.y = topo - 70
    pdf.linha(pdf.m, pdf.y, pdf.L - pdf.m, pdf.y, ESCURO, 1)
    pdf.espaco(8)

    presentes = [p for p in r["parls"] if p["id"] in r["presenca"]]
    ausentes = [p for p in r["parls"] if p["id"] not in r["presenca"]]
    falas = [h for h in r["hist"]]
    votacoes = list(reversed(r["votacoes"]))
    resumo = (f"Presentes {len(presentes)} de {len(r['parls'])} parlamentares. {len(falas)} pronunciamento(s), "
              f"{len(votacoes)} votação(ões), {len(r['ocorrencias'])} ocorrência(s) registrada(s).")
    pdf.paragrafo(resumo, 9.5, False, CINZA)

    # 1. presença
    n = 1
    pdf.secao(n, "Presença")
    minimo = len(r["parls"]) // 2 + 1
    pdf.paragrafo(f"Quórum: {len(presentes)} presentes; maioria absoluta de {minimo}. "
                  + ("Quórum atingido." if len(presentes) >= minimo else "Quórum NÃO atingido."), 9.5)
    pdf.espaco(4)
    if presentes:
        pdf.tabela([("Parlamentar", .55, "e"), ("Partido", .25, "e"), ("Chegada", .2, "c")],
                   [[p["nome"] + (" (líder)" if p.get("lider") else ""), p["partido"], _hora(r["presenca"][p["id"]])]
                    for p in sorted(presentes, key=lambda p: r["presenca"][p["id"]])])
    if ausentes:
        pdf.espaco(6)
        pdf.paragrafo("Ausentes: " + ", ".join(f"{p['nome']} ({p['partido']})" for p in ausentes) + ".", 9, False, CINZA)

    # 2. pauta
    n += 1
    pdf.secao(n, "Pauta")
    if not r["pauta"]:
        pdf.paragrafo("Sem matérias na pauta.", 9.5, False, CINZA)
    for chave, titulo in SECOES_PAUTA.items():
        itens = [m for m in r["pauta"] if m.get("secao", "ordem_do_dia") == chave]
        if itens:
            pdf.garantir(46)
            pdf.paragrafo(titulo, 10, True)
            pdf.tabela([("Matéria", .17, "e"), ("Ementa", .5, "e"), ("Autoria", .19, "e"), ("Situação", .14, "e")],
                       [[m["numero"], m["ementa"], m.get("autor") or "—", SITUACOES.get(m["situacao"], m["situacao"])]
                        for m in itens])
            pdf.espaco(6)

    # 3. pronunciamentos
    n += 1
    pdf.secao(n, "Pronunciamentos")
    if falas:
        linhas = []
        for h in reversed(falas):
            disp = h["previsto"] + h.get("bonus", 0) + h.get("extra", 0)
            obs = [f"{TIPOS.get(a['tipo'], a['tipo'])}: {a['nome']} ({_dur(a['usado'])})" for a in h.get("apartes", [])]
            obs += [f"Tempo cedido por {c}" for c in h.get("cedentes", [])]
            if h.get("extra"):
                obs.append(f"{'+' if h['extra'] > 0 else ''}{h['extra']} s concedidos")
            m = materias.get(h.get("materia"))
            linhas.append([_hora(h["inicio"]), f"{h['nome']} ({h['partido']})", h["faseNome"] + (f" · {m['numero']}" if m else ""),
                           f"{_dur(h['usado'])} / {_dur(disp)}", "; ".join(obs) or "—"])
        pdf.tabela([("Hora", .08, "c"), ("Parlamentar", .25, "e"), ("Fase / matéria", .24, "e"), ("Usado / disp.", .13, "c"),
                    ("Apartes e observações", .30, "e")], linhas)
        # tempo por partido
        por_partido: dict[str, int] = {}
        for h in falas:
            por_partido[h["partido"]] = por_partido.get(h["partido"], 0) + h["usado"]
        pdf.espaco(8)
        pdf.paragrafo("Tempo de fala por partido: " + "; ".join(
            f"{p} {_dur(s)}" for p, s in sorted(por_partido.items(), key=lambda x: -x[1])) + ".", 9, False, CINZA)
    else:
        pdf.paragrafo("Nenhum pronunciamento registrado.", 9.5, False, CINZA)

    # 4. votações
    n += 1
    pdf.secao(n, "Votações")
    if not votacoes:
        pdf.paragrafo("Nenhuma votação realizada.", 9.5, False, CINZA)
    for k, v in enumerate(votacoes, 1):
        pdf.garantir(70)
        pdf.espaco(4)
        pdf.paragrafo(f"{n}.{k}  {v['numero']} — {v['ementa']}", 10, True)
        c = v.get("contagem") or {}
        placar = "" if v["tipo"] == "simbolica" else f" Sim {c.get('sim', 0)}, Não {c.get('nao', 0)}, Abstenção {c.get('abst', 0)}."
        pdf.paragrafo(f"Votação {PROCESSOS.get(v['tipo'], v['tipo'])}, quórum de {QUORUNS.get(v['quorum'], v['quorum'])}, "
                      f"encerrada às {_hora(v.get('fim'))}.{placar} Resultado: {SITUACOES.get(v['resultado'], v['resultado']).upper()}.", 9.5)
        if v["tipo"] == "nominal" and v.get("votos"):
            pdf.espaco(4)
            linhas = [[nome(pid), parls.get(pid, {}).get("partido", ""), VOTOS.get(x, x)] for pid, x in v["votos"].items()]
            linhas.sort(key=lambda l: (["Sim", "Não", "Abstenção"].index(l[2]) if l[2] in ("Sim", "Não", "Abstenção") else 9, l[0]))
            pdf.tabela([("Parlamentar", .55, "e"), ("Partido", .25, "e"), ("Voto", .2, "c")], linhas)
        if v.get("manifestacoes"):
            pdf.espaco(4)
            pdf.paragrafo("Manifestações escritas (sessão virtual):", 9.5, True)
            for m in v["manifestacoes"]:
                pdf.paragrafo(f"{m['nome']}, {_hora(m['ts'])} de {time.strftime('%d/%m', time.localtime(m['ts'] / 1000))}: {m['texto']}",
                              9, False, ESCURO, recuo=10)
        if v["tipo"] == "secreta":
            pdf.paragrafo(f"Votação secreta: {len(v.get('votantes', []))} parlamentares votaram; os votos individuais não são registrados.", 9, False, CINZA)
        pdf.espaco(6)

    # 5. ocorrências
    n += 1
    pdf.secao(n, "Ocorrências")
    if r["ocorrencias"]:
        pdf.tabela([("Hora", .1, "c"), ("Ocorrência", .24, "e"), ("Parlamentar", .28, "e"), ("Motivo", .38, "e")],
                   [[_hora(o["ts"]), OCORRENCIAS.get(o["tipo"], o["tipo"]), o.get("nome") or "—", o.get("motivo") or "—"]
                    for o in reversed(r["ocorrencias"])])
    else:
        pdf.paragrafo("Nenhuma ocorrência registrada.", 9.5, False, CINZA)

    if r.get("andamento"):
        n += 1
        pdf.secao(n, "Linha do tempo da sessão")
        pdf.tabela([("Hora", .15, "c"), ("Registro", .85, "e")],
                   [[_hora(e["ts"]), e["rotulo"]] for e in r["andamento"]])

    # 6. gravações
    gravs = r.get("gravacoes") or []
    if gravs:
        n += 1
        pdf.secao(n, "Gravações")
        pdf.tabela([("Início", .12, "c"), ("Orador", .38, "e"), ("Fase", .3, "e"), ("Duração", .2, "c")],
                   [[_hora(g["inicio"]), f"{g['nome']} ({g['partido']})", g.get("faseNome") or "—",
                     _dur(((g.get("fim") or g["ultimo"]) - g["criado"]) / 1000)] for g in reversed(gravs)])
        pdf.paragrafo("Os arquivos de áudio ficam arquivados no servidor do Parlatório Legis.", 8.5, False, CINZA)

    # resumo dos trabalhos (rascunho por IA, revisado pela secretaria)
    itens_resumo = [i for i in (r.get("resumoIA") or {}).get("itens", []) if (i.get("texto") or "").strip()]
    if itens_resumo:
        n += 1
        pdf.secao(n, "Resumo dos trabalhos")
        if any(not i.get("revisado") for i in itens_resumo):
            pdf.paragrafo("Itens marcados como rascunho foram redigidos automaticamente a partir das transcrições e "
                          "ainda não foram revisados pela secretaria.", 8.5, False, CINZA)
        for i in itens_resumo:
            pdf.garantir(50)
            pdf.espaco(4)
            pdf.paragrafo(i["titulo"], 10, True)
            if not i.get("revisado"):
                pdf.paragrafo("Rascunho automático, não revisado.", 8.5, False, CINZA)
            pdf.paragrafo(i["texto"], 9.5, False, ESCURO, recuo=10)

    # 7. íntegra dos pronunciamentos (transcrição)
    transcritas = [g for g in reversed(gravs) if ((g.get("transcricao") or {}).get("texto") or "").strip()]
    if transcritas:
        n += 1
        pdf.secao(n, "Íntegra dos pronunciamentos")
        if any(not g["transcricao"].get("revisada") for g in transcritas):
            pdf.paragrafo("Trechos marcados como transcrição automática não foram revisados e podem conter erros; "
                          "em caso de dúvida, vale o áudio.", 8.5, False, CINZA)
        for g in transcritas:
            t = g["transcricao"]
            pdf.garantir(60)
            pdf.espaco(6)
            pdf.paragrafo(f"{_hora(g['inicio'])} · {g['nome']} ({g['partido']})" + (f" · {g['faseNome']}" if g.get("faseNome") else ""),
                          10, True)
            if not t.get("revisada"):
                pdf.paragrafo("Transcrição automática, não revisada.", 8.5, False, CINZA)
            for bloco in [b.strip() for b in t["texto"].split("\n") if b.strip()]:
                pdf.paragrafo(bloco, 9.5, False, ESCURO, recuo=10)

    # assinaturas
    pdf.garantir(90)
    pdf.espaco(48)
    meio = pdf.L / 2
    for x0 in (pdf.m + 10, meio + 20):
        pdf.linha(x0, pdf.y, x0 + 210, pdf.y, ESCURO, 0.8)
    pdf.texto(pdf.m + 10 + (210 - largura("Presidente", 9)) / 2, pdf.y - 13, "Presidente", 9)
    pdf.texto(meio + 20 + (210 - largura("Secretário(a)", 9)) / 2, pdf.y - 13, "Secretário(a)", 9)
    return pdf.gerar()
