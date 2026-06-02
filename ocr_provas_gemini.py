"""
OCR de provas CA-AA-AFN usando Gemini Vision API
Uso: python ocr_provas_gemini.py SUA_API_KEY

Requer: pip install google-generativeai pymupdf pillow
"""

import os, sys, json, re, time, datetime
import fitz          # pymupdf
from PIL import Image
import io

sys.stdout.reconfigure(encoding='utf-8')

PASTA  = r"C:\Users\alexa\Documents\Estudos"
SAIDA  = os.path.join(PASTA, "banco_questoes.json")

RANGES = {
    'portugues':  (1,  8),
    'matematica': (9,  15),
    'geografia':  (16, 23),
    'historia':   (24, 30),
    'militar':    (31, 50),
}
NOMES = {
    'portugues':  'Portugues',
    'matematica': 'Matematica',
    'geografia':  'Geografia Economica',
    'historia':   'Historia Militar-Naval',
    'militar':    'Conhecimento Militar-Naval',
}

def materia_da_questao(num):
    for mat, (ini, fim) in RANGES.items():
        if ini <= num <= fim:
            return mat
    return 'desconhecida'

# ─── PDF → Imagens ────────────────────────────────────────────
def pdf_para_imagens(caminho, dpi=200):
    """Converte cada página do PDF em imagem PIL."""
    doc = fitz.open(caminho)
    imagens = []
    for page in doc:
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        imagens.append(img)
    return imagens

# ─── OCR com Gemini ──────────────────────────────────────────
PROMPT_OCR = """Você está fazendo OCR de uma página de prova de concurso militar (CA-AA-AFN da Marinha do Brasil).

Extraia SOMENTE o texto das questões presentes nesta imagem, mantendo EXATAMENTE este formato para cada questão:

QUESTÃO N
[enunciado completo da questão]
A) [texto da alternativa A]
B) [texto da alternativa B]
C) [texto da alternativa C]
D) [texto da alternativa D]
E) [texto da alternativa E]

Regras:
- Mantenha o número original da questão (ex: QUESTÃO 1, QUESTÃO 15, QUESTÃO 31)
- Copie o enunciado EXATO, incluindo textos de apoio, poemas, tabelas etc.
- Copie as alternativas EXATAS
- Se não houver questão nesta página (capa, instruções), responda apenas: SEM_QUESTAO
- Não adicione comentários, apenas o texto extraído"""

def ocr_pagina_gemini(model, img):
    """Faz OCR de uma imagem PIL usando Gemini Vision."""
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    img_bytes = buf.read()

    import google.generativeai as genai
    img_part = {"mime_type": "image/png", "data": img_bytes}

    for tentativa in range(3):
        try:
            response = model.generate_content([PROMPT_OCR, img_part])
            return response.text
        except Exception as e:
            print(f"    Erro (tentativa {tentativa+1}): {e}")
            time.sleep(3)
    return ""

# ─── Parser de texto OCR → questões estruturadas ─────────────
def parsear_texto_ocr(texto, ano, arquivo):
    questoes = []
    texto = texto.replace('\r\n', '\n').replace('\r', '\n')

    pat_q = re.compile(
        r'(?:^|\n)\s*QUEST[ÃA]O\s+(\d{1,2})\s*\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(pat_q.finditer(texto))

    pat_alt = re.compile(
        r'(?:^|\n)\s*([A-E])\s*\)\s*(.+?)(?=(?:\n\s*[A-E]\s*\))|\Z)',
        re.DOTALL | re.MULTILINE
    )

    for i, m in enumerate(matches):
        num = int(m.group(1))
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
        if num < 1 or num > 50:
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

# ─── Parser de gabarito ──────────────────────────────────────
def parsear_gabarito_txt(texto):
    gab = {}
    pat = re.compile(r'(?<!\d)0*([1-9]|[1-4][0-9]|50)\s*[-.\):]\s*([A-Ea-e])(?!\w)', re.MULTILINE)
    for m in pat.finditer(texto):
        num = int(m.group(1))
        if 1 <= num <= 50:
            gab[num] = m.group(2).upper()
    return gab

# ─── MAIN ────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Uso: python ocr_provas_gemini.py SUA_GEMINI_API_KEY")
        print("     Obtenha em: https://aistudio.google.com/apikey")
        return

    api_key = sys.argv[1]

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')

    print("Gemini conectado!")

    pdfs = [f for f in os.listdir(PASTA) if f.lower().endswith('.pdf')]
    gab_files   = [f for f in pdfs if re.search(r'gabarito|gab_div', f, re.I)]
    prova_files = [f for f in pdfs if re.search(r'prova_\d{4}', f, re.I)]

    # ── gabaritos via pdfplumber (texto normal) ──
    import pdfplumber
    gabaritos_por_ano = {}
    for gf in gab_files:
        caminho = os.path.join(PASTA, gf)
        texto = ""
        try:
            with pdfplumber.open(caminho) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        texto += t + "\n"
        except:
            pass
        m = re.search(r'(202[0-9])', gf + texto[:500])
        if m:
            ano = int(m.group(1))
            g = parsear_gabarito_txt(texto)
            if ano in gabaritos_por_ano:
                gabaritos_por_ano[ano].update(g)
            else:
                gabaritos_por_ano[ano] = g
            print(f"Gabarito {ano}: {len(g)} respostas -- {gf}")

    # ── provas via OCR ──
    todas_questoes = []
    for pf in sorted(prova_files):
        caminho = os.path.join(PASTA, pf)
        m = re.search(r'(202[0-9])', pf)
        ano = int(m.group(1)) if m else None
        if not ano:
            print(f"Ano nao detectado: {pf}")
            continue

        print(f"\nOCR: {pf} (ano {ano})")

        # cache: se ja existe arquivo .txt intermediario, reutiliza
        cache_path = os.path.join(PASTA, f"ocr_cache_{ano}.txt")
        if os.path.exists(cache_path):
            print(f"  Usando cache: {cache_path}")
            with open(cache_path, encoding='utf-8') as f:
                texto_total = f.read()
        else:
            imagens = pdf_para_imagens(caminho)
            print(f"  {len(imagens)} paginas para OCR...")
            textos = []
            for i, img in enumerate(imagens):
                print(f"  Pagina {i+1}/{len(imagens)}...", end=' ', flush=True)
                t = ocr_pagina_gemini(model, img)
                if t and 'SEM_QUESTAO' not in t:
                    textos.append(t)
                    print(f"OK ({len(t)} chars)")
                else:
                    print("(sem questao)")
                time.sleep(1)  # rate limit
            texto_total = "\n\n".join(textos)
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(texto_total)
            print(f"  Cache salvo: {cache_path}")

        questoes = parsear_texto_ocr(texto_total, ano, pf)

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

        print(f"  {len(questoes)} questoes | {con_gab} com gabarito")
        for mat, n in sorted(por_mat.items(), key=lambda x: RANGES[x[0]][0] if x[0] in RANGES else 99):
            esperado = RANGES[mat][1] - RANGES[mat][0] + 1 if mat in RANGES else '?'
            ok = "OK" if n == esperado else f"ATENCAO esperado {esperado}"
            print(f"    {NOMES.get(mat,mat)}: {n}q {ok}")

        todas_questoes.extend(questoes)

    # ── salva ──
    todas_questoes.sort(key=lambda q: (q['ano'], q['num']))
    anos = sorted(set(q['ano'] for q in todas_questoes))
    total = len(todas_questoes)
    com_gab = sum(1 for q in todas_questoes if q['gabarito'])

    banco = {
        "versao":   "1.0",
        "geradoEm": datetime.datetime.now().isoformat(),
        "total":    total,
        "anos":     anos,
        "questoes": todas_questoes,
    }

    with open(SAIDA, 'w', encoding='utf-8') as f:
        json.dump(banco, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*55}")
    print(f"TOTAL: {total} questoes | {com_gab} com gabarito")
    print(f"Anos:  {anos}")
    print(f"Salvo: {SAIDA}")
    print("Banco gerado com sucesso!")

if __name__ == '__main__':
    main()
