r"""
Extrator de banco de questões — CA-AA-AFN
Lê os PDFs com OCR diretamente de C:\Users\alexa\Documents\Estudos\provas (recursivo)
Saída: banco_questoes.json na pasta C:\Users\alexa\Documents\Estudos
"""

import os, re, json, sys, datetime
import pdfplumber

sys.stdout.reconfigure(encoding='utf-8')

PASTA  = r"C:\Users\alexa\Documents\Estudos\provas"
SAIDA  = r"C:\Users\alexa\Documents\Estudos\banco_questoes.json"

# Distribuição real CA-AA-AFN
RANGES = {
    'portugues':  (1,  8),
    'matematica': (9,  15),
    'geografia':  (16, 23),
    'historia':   (24, 30),
    'militar':    (31, 50),
}

NOMES = {
    'portugues':  'Português',
    'matematica': 'Matemática',
    'geografia':  'Geografia Econômica',
    'historia':   'História Militar-Naval',
    'militar':    'Conhecimento Militar-Naval',
}

def materia_da_questao(num):
    for mat, (ini, fim) in RANGES.items():
        if ini <= num <= fim:
            return mat
    return 'desconhecida'

def extrair_texto_pdf(caminho):
    linhas = []
    try:
        with pdfplumber.open(caminho) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    linhas.append(t)
    except Exception as e:
        print(f"  ⚠️  Erro: {e}")
    return "\n".join(linhas)

def detectar_ano(nome, texto):
    for src in [nome, texto[:1000]]:
        m = re.search(r'(20[1-2][0-9])', src)
        if m:
            return int(m.group(1))
    return None

# ─── PARSER DE GABARITO ────────────────────────────────────────
def parsear_gabarito(texto, ano):
    gab = {}
    pat = re.compile(
        r'(?<!\d)0*([1-9]|[1-4][0-9]|50)\s*[-\.\):\s]+\s*([A-Ea-e])(?!\w)',
        re.MULTILINE
    )
    for m in pat.finditer(texto):
        num   = int(m.group(1))
        letra = m.group(2).upper()
        if 1 <= num <= 50:
            gab[num] = letra
    return gab

# ─── PARSER DE QUESTÕES ────────────────────────────────────────
def parsear_questoes(texto, ano, arquivo):
    questoes = []
    texto = texto.replace('\r\n', '\n').replace('\r', '\n')

    # Tolerância a caracteres inválidos no OCR como QUESTO
    pat_q = re.compile(
        r'(?:^|\n)[ \t]*(?:QUEST.O|Quest.o|QUESTAO|Questao)[\s\-\.]*0*(\d{1,2})\b',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(pat_q.finditer(texto))

    if len(matches) < 10:
        pat_q2 = re.compile(
            r'(?:^|\n)[ \t]*0*((?:[1-4][0-9]|50|[1-9]))[ \t]*[–\-\.\)][ \t]',
            re.MULTILINE
        )
        matches2 = list(pat_q2.finditer(texto))
        if len(matches2) > len(matches):
            matches = matches2
            print(f"    (usando padrão fallback: {len(matches)} matches)")

    print(f"  📌 {len(matches)} questões detectadas")

    pat_alt = re.compile(
        r'(?:^|\n)[ \t]*(?:\()?[ \t]*([A-E])[ \t]*[\)\-\.][ \t]*(.+?)(?=(?:\n[ \t]*(?:\()?[ \t]*[A-E][ \t]*[\)\-\.])|\Z)',
        re.DOTALL | re.MULTILINE | re.IGNORECASE
    )

    for i, m in enumerate(matches):
        nums = re.findall(r'\d+', m.group(0))
        if not nums:
            continue
        num = int(nums[-1])
        if num < 1 or num > 50:
            continue

        ini = m.end()
        fim = matches[i+1].start() if i+1 < len(matches) else len(texto)
        bloco = texto[ini:fim].strip()

        alt_matches = list(pat_alt.finditer(bloco))
        if alt_matches:
            enunciado = bloco[:alt_matches[0].start()].strip()
            alternativas = {}
            for am in alt_matches:
                l = am.group(1).upper()
                t = re.sub(r'\s+', ' ', am.group(2)).strip()
                alternativas[l] = t
        else:
            enunciado = bloco
            alternativas = {}

        enunciado = re.sub(r'\s+', ' ', enunciado).strip()
        if len(enunciado) < 5:
            continue

        mat = materia_da_questao(num)
        questoes.append({
            "id":           f"q{ano}-{num:02d}",
            "ano":          ano,
            "num":          num,
            "materia":      mat,
            "materiaNome":  NOMES.get(mat, mat),
            "enunciado":    enunciado,
            "alternativas": alternativas,
            "gabarito":     "",
            "topico":       "",
            "fonte":        os.path.basename(arquivo),
        })

    return questoes

# ─── MAIN ──────────────────────────────────────────────────────
def main():
    pdfs = []
    for root, dirs, files in os.walk(PASTA):
        for f in files:
            if f.lower().endswith('.pdf'):
                pdfs.append(os.path.join(root, f))

    gab_files = [f for f in pdfs if re.search(r'gabarito|gab_|gab final|final\.pdf', os.path.basename(f), re.I) and not re.search(r'correc|correção', os.path.basename(f), re.I)]
    prova_files = [f for f in pdfs if not re.search(r'gabarito|gab_|gab final|correc|correção|caderno de exerc|final\.pdf', os.path.basename(f), re.I)]

    print(f"📂 Pasta: {PASTA}")
    print(f"  Provas ({len(prova_files)}):")
    for p in prova_files: print(f"    - {os.path.basename(p)}")
    print(f"\n  Gabaritos ({len(gab_files)}):")
    for g in gab_files: print(f"    - {os.path.basename(g)}")
    print()

    # ── gabaritos ──
    gabaritos_por_ano = {}
    for gf in gab_files:
        texto = extrair_texto_pdf(gf)
        ano = detectar_ano(os.path.basename(gf), texto)
        if ano:
            g = parsear_gabarito(texto, ano)
            if ano in gabaritos_por_ano:
                gabaritos_por_ano[ano].update(g)
            else:
                gabaritos_por_ano[ano] = g
            print(f"  ✅ Gabarito {ano}: {len(g)} respostas — {os.path.basename(gf)}")
        else:
            print(f"  ⚠️  Ano não detectado em: {os.path.basename(gf)}")

    # ── provas ──
    todas_questoes = []
    for pf in sorted(prova_files):
        print(f"\n🗂️  {os.path.basename(pf)}")
        texto = extrair_texto_pdf(pf)
        ano = detectar_ano(os.path.basename(pf), texto)
        if not ano:
            print(f"  ⚠️  Ano não detectado")
            continue

        questoes = parsear_questoes(texto, ano, pf)

        gab = gabaritos_por_ano.get(ano, {})
        con_gab = 0
        for q in questoes:
            g = gab.get(q['num'], '')
            q['gabarito'] = g
            if g:
                con_gab += 1

        por_mat = {}
        for q in questoes:
            por_mat[q['materia']] = por_mat.get(q['materia'], 0) + 1
        print(f"  ✅ {len(questoes)} questões | {con_gab} com gabarito | ano {ano}")
        for mat, n in sorted(por_mat.items(), key=lambda x: RANGES[x[0]][0] if x[0] in RANGES else 99):
            esperado = RANGES[mat][1] - RANGES[mat][0] + 1 if mat in RANGES else '?'
            ok = "✓" if n == esperado else f"⚠️ esperado {esperado}"
            print(f"     {NOMES.get(mat,mat)}: {n}q {ok}")

        todas_questoes.extend(questoes)

    # ── salva ──
    todas_questoes.sort(key=lambda q: (q['ano'], q['num']))
    anos = sorted(set(q['ano'] for q in todas_questoes))
    total = len(todas_questoes)
    com_gab = sum(1 for q in todas_questoes if q['gabarito'])

    banco = {
        "versao":    "1.0",
        "geradoEm":  datetime.datetime.now().isoformat(),
        "total":     total,
        "anos":      anos,
        "questoes":  todas_questoes,
    }

    with open(SAIDA, 'w', encoding='utf-8') as f:
        json.dump(banco, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*55}")
    print(f"📊 TOTAL: {total} questões | {com_gab} com gabarito")
    print(f"   Anos:  {anos}")
    print(f"   JSON:  {SAIDA}")
    print(f"✅ Banco de questões gerado com sucesso!")

if __name__ == '__main__':
    main()
