import os
import sys
import json
import time
import re
import datetime
import pdfplumber
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from google import genai
from google.genai import types

# Force stdout/stderr to use UTF-8 encoding
sys.stdout.reconfigure(encoding='utf-8')

# Constants and Paths
BASE_DIR = r"C:\Users\alexa\Documents\Estudos\CA-AA-AFN"
OUTPUT_CONSOLIDADO = os.path.join(BASE_DIR, "banco_completo.json")
OUTPUT_STATS = os.path.join(BASE_DIR, "estatisticas.json")
CACHE_FILE = os.path.join(BASE_DIR, "cache_extracao.json")

# Load Gemini API Key dynamically
API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not API_KEY:
    _key_file = os.path.join(os.path.dirname(__file__), "key.txt")
    if os.path.exists(_key_file):
        try:
            with open(_key_file, "r", encoding="utf-8") as f:
                API_KEY = f.read().strip()
        except Exception:
            pass


# Global flag to track API quota exhaustion
API_QUOTA_EXHAUSTED = False

# Map subjects and ranges
SUBJECT_MAP = [
    (1, 8, "Língua Portuguesa", "portugues", "Português"),
    (9, 15, "Matemática", "matematica", "Matemática"),
    (16, 23, "Geografia", "geografia", "Geografia Econômica"),
    (24, 30, "História", "historia", "História Militar-Naval"),
    (31, 50, "Conhecimento Militar-Naval", "militar", "Conhecimento Militar-Naval")
]

def get_subject_info(num: int):
    for ini, fim, disciplina, materia, materiaNome in SUBJECT_MAP:
        if ini <= num <= fim:
            return disciplina, materia, materiaNome
    return "Desconhecida", "desconhecida", "Desconhecida"

# Exam Meta Specs for each year
EXAM_SPECS = {
    2019: {"prova": "Verde", "candidatos": "CAP"},
    2020: {"prova": "Verde", "candidatos": "CPA-CAP"},
    2021: {"prova": "Verde", "candidatos": "CPA-CAP"},
    2022: {"prova": "Verde", "candidatos": "CPA"},
    2023: {"prova": "Amarela", "candidatos": "CPA-CAP"},
    2024: {"prova": "Amarela", "candidatos": "CPA-CAP"}
}

# Define the Pydantic models for structured output
class AlternativasModel(BaseModel):
    A: str = Field(description="Texto da alternativa A")
    B: str = Field(description="Texto da alternativa B")
    C: str = Field(description="Texto da alternativa C")
    D: str = Field(description="Texto da alternativa D")
    E: str = Field(description="Texto da alternativa E")

class QuestionModel(BaseModel):
    num: int = Field(description="Número da questão na prova")
    disciplina: str = Field(description="Disciplina da questão ('Língua Portuguesa', 'Matemática', 'Geografia', 'História', 'Conhecimento Militar-Naval')")
    assunto: str = Field(description="Assunto principal da questão")
    subassunto: str = Field(description="Subassunto específico da questão")
    enunciado: str = Field(description="Enunciado da questão (use LaTeX para fórmulas)")
    alternativas: AlternativasModel = Field(description="As 5 alternativas de A a E")
    dificuldade: str = Field(description="Dificuldade da questão ('facil', 'media', 'dificil')")
    comentario_gabarito: str = Field(description="Explicação rápida do gabarito ou resolução da questão")
    texto_base: str = Field(description="Texto base, apoio ou poema usado pela questão (se aplicável, senão string vazia)")
    tags: List[str] = Field(description="Lista de tags ou palavras-chave relacionadas")

class QuestionListModel(BaseModel):
    questoes: List[QuestionModel] = Field(description="Lista de questões extraídas")

# Load Cache
def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load cache: {e}")
    return {}

# Save Cache
def save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save cache: {e}")

# Parse Answer Key (Gabarito) from PDF text
def parse_gabarito(ano: int, pdf_path: str) -> dict:
    print(f"Parsing answer key for year {ano}...")
    answers = {}
    
    spec = EXAM_SPECS.get(ano)
    if not spec:
        print(f"No specifications found for year {ano}")
        return answers
        
    color_target = spec["prova"].upper()  # VERDE or AMARELA
    candidatos_target = spec["candidatos"]  # CAP, CPA-CAP, CPA
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        print(f"Error opening gabarito for {ano}: {e}")
        return answers
        
    # We will extract the text block that starts with the target candidate line
    cand_matches = list(re.finditer(r'Candidatos\s+oriundos\s+', text, re.I))
    target_block = ""
    for idx, match in enumerate(cand_matches):
        block_header = text[match.start():match.start()+100].upper()
        match_group = False
        if candidatos_target == "CAP" and "CAP" in block_header and "CPFN" not in block_header:
            match_group = True
        elif candidatos_target == "CPA-CAP" and ("CPA" in block_header or "CAP" in block_header) and "CPFN" not in block_header:
            match_group = True
        elif candidatos_target == "CPA" and "CPA" in block_header and "CAP" not in block_header and "CPFN" not in block_header:
            match_group = True
            
        if match_group:
            end_pos = cand_matches[idx+1].start() if idx+1 < len(cand_matches) else len(text)
            target_block = text[match.start():end_pos]
            break
            
    if not target_block:
        target_block = text
        
    lines = target_block.split("\n")
    
    for line in lines:
        line_clean = re.sub(r'\s+', ' ', line).strip()
        if not line_clean:
            continue
            
        matches = list(re.finditer(r'(\d+)\s*-\s*([A-E]|ANULADA|ALTERADA\s+PARA\s+[A-E])', line_clean, re.I))
        
        if len(matches) == 4:
            if color_target == "VERDE":
                target_matches = [matches[2], matches[3]]
            else:
                target_matches = [matches[0], matches[1]]
        elif len(matches) == 2:
            target_matches = [matches[0], matches[1]]
        else:
            target_matches = matches
            
        for m in target_matches:
            num = int(m.group(1))
            ans_str = m.group(2).upper()
            
            letter = ""
            is_anulada = False
            
            if "ANULADA" in ans_str:
                is_anulada = True
            elif "ALTERADA" in ans_str:
                m_let = re.search(r'PARA\s+([A-E])', ans_str)
                if m_let:
                    letter = m_let.group(1)
                else:
                    letter = ans_str[-1]
            else:
                letter = ans_str.strip()
                
            if 1 <= num <= 50:
                answers[num] = {"gabarito": letter if not is_anulada else "", "anulada": is_anulada}
                
    print(f"Parsed {len(answers)} answers for year {ano}: {sorted(list(answers.keys()))}")
    return answers

# Clean unescaped backslashes in JSON strings returned by the LLM
def fix_json_backslashes(json_str: str) -> str:
    placeholder = "___DOUBLE_BACKSLASH_PLACEHOLDER___"
    s = json_str.replace("\\\\", placeholder)
    s = re.sub(r'\\(?!["nrtu])', r'\\\\', s)
    s = s.replace(placeholder, "\\\\")
    return s

# Clean headers/footers from question/alternative texts
def clean_extracted_text(text: str) -> str:
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line_clean = line.strip()
        # Skip headers, footers, page numbers and column markers
        if re.search(r'^(Prova\s*:|Candidatos\s+oriundos|---\s+\w+\s+COLUMN|Página\s*:|CA-AA-AFN|---\s+PÁGINA)', line_clean, re.I):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()

# Local parser helper functions
def extract_raw_question_text(full_text: str, num: int) -> str:
    pattern = rf'(QUEST[ÃA]O\s+{num}\b)'
    match = re.search(pattern, full_text, re.IGNORECASE)
    if not match:
        return ""
    start_pos = match.start()
    
    # Find next question start
    next_pattern = rf'(QUEST[ÃA]O\s+{num+1}\b)'
    next_match = re.search(next_pattern, full_text[start_pos+10:], re.IGNORECASE)
    if next_match:
        end_pos = start_pos + 10 + next_match.start()
    else:
        end_pos = len(full_text)
        
    return full_text[start_pos:end_pos].strip()

def parse_alternatives_from_text(q_text: str):
    parts = re.split(r'\(([A-E])\)', q_text)
    if len(parts) >= 11:
        enunciado = parts[0].strip()
        enunciado = re.sub(r'^QUEST[ÃA]O\s+\d+\s*', '', enunciado, flags=re.I).strip()
        alternativas = {
            "A": parts[2].strip(),
            "B": parts[4].strip(),
            "C": parts[6].strip(),
            "D": parts[8].strip(),
            "E": parts[10].strip()
        }
        return enunciado, alternativas
    
    parts_alt = re.split(r'\b([A-E])\)', q_text)
    if len(parts_alt) >= 11:
        enunciado = parts_alt[0].strip()
        enunciado = re.sub(r'^QUEST[ÃA]O\s+\d+\s*', '', enunciado, flags=re.I).strip()
        alternativas = {
            "A": parts_alt[2].strip(),
            "B": parts_alt[4].strip(),
            "C": parts_alt[6].strip(),
            "D": parts_alt[8].strip(),
            "E": parts_alt[10].strip()
        }
        return enunciado, alternativas
    return q_text.strip(), None

# Python-only fallback for questions
def extract_question_fallback(ano: int, num: int, raw_q_text: str) -> dict:
    disciplina, materia, materiaNome = get_subject_info(num)
    
    enunciado, alternativas = parse_alternatives_from_text(raw_q_text)
    if not alternativas:
        enunciado = clean_extracted_text(raw_q_text)
        alternativas = {
            "A": "Alternativa A",
            "B": "Alternativa B",
            "C": "Alternativa C",
            "D": "Alternativa D",
            "E": "Alternativa E"
        }
    else:
        enunciado = clean_extracted_text(enunciado)
        alternativas = {k: clean_extracted_text(v) for k, v in alternativas.items()}
        
    fallback_comentarios = {
        "portugues": "Explicação detalhada disponível no material de estudos de Língua Portuguesa.",
        "matematica": "Resolução detalhada disponível no material de estudos de Matemática. Por favor, consulte o gabarito oficial.",
        "geografia": "Explicação disponível no material de estudos de Geografia.",
        "historia": "Explicação disponível no material de estudos de História.",
        "militar": "Explicação disponível no material de estudos de Conhecimento Militar-Naval."
    }
    
    return {
        "num": num,
        "disciplina": disciplina,
        "assunto": "Geral",
        "subassunto": "Geral",
        "enunciado": enunciado,
        "alternativas": alternativas,
        "dificuldade": "media",
        "comentario_gabarito": fallback_comentarios.get(materia, "Consulte o gabarito oficial."),
        "texto_base": "",
        "tags": [materia, "marinha"]
    }

# Main function to extract questions using Gemini API
def extrair_prova_ano(client, cache, ano: int, pdf_path: str, gabaritos: dict) -> List[dict]:
    global API_QUOTA_EXHAUSTED
    print(f"\n==========================================")
    print(f"Processing EXAM YEAR {ano}...")
    print(f"==========================================")
    
    spec = EXAM_SPECS[ano]
    questoes_ano = []
    
    # Extract the full exam text locally by splitting pages vertically
    print("Extracting exam text locally via vertical splitting...")
    full_exam_text_lines = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Skip page 0 (cover page) and process the rest
            for idx, page in enumerate(pdf.pages[1:]):
                width = page.width
                height = page.height
                
                # Split page vertically into left and right columns
                left = page.crop((0, 0, width / 2, height))
                right = page.crop((width / 2, 0, width, height))
                
                left_text = left.extract_text() or ""
                right_text = right.extract_text() or ""
                
                full_exam_text_lines.append(f"--- PÁGINA {idx+2} (COLUNA ESQUERDA) ---")
                full_exam_text_lines.append(left_text)
                full_exam_text_lines.append(f"--- PÁGINA {idx+2} (COLUNA DIREITA) ---")
                full_exam_text_lines.append(right_text)
    except Exception as e:
        print(f"[FATAL] Error reading PDF page text for {ano}: {e}")
        sys.exit(1)
        
    full_exam_text = "\n\n".join(full_exam_text_lines)
    print(f"Extraction complete. Total text size: {len(full_exam_text)} characters.")
    
    # We will query in chunks of 5 questions
    chunks = [
        (1, 5), (6, 10), (11, 15), (16, 20), (21, 25),
        (26, 30), (31, 35), (36, 40), (41, 45), (46, 50)
    ]
    
    for ini, fim in chunks:
        cache_key = f"{ano}_{ini}_{fim}"
        if cache_key in cache:
            print(f"  [CACHE] Using cached extraction for Q{ini}-Q{fim} of year {ano}")
            questoes_ano.extend(cache[cache_key])
            continue
            
        if API_QUOTA_EXHAUSTED:
            print(f"  [QUOTA EXHAUSTED] Directly using local fallback for Q{ini}-Q{fim}...")
            chunk_qs = []
            for num in range(ini, fim + 1):
                single_cache_key = f"{ano}_{num}"
                if single_cache_key in cache:
                    chunk_qs.append(cache[single_cache_key])
                else:
                    raw_q_text = extract_raw_question_text(full_exam_text, num)
                    q_parsed = extract_question_fallback(ano, num, raw_q_text)
                    cache[single_cache_key] = q_parsed
                    save_cache(cache)
                    chunk_qs.append(q_parsed)
            questoes_ano.extend(chunk_qs)
            continue
            
        print(f"  Extracting questions {ini} to {fim}...")
        
        prompt = (
            "Você é um especialista em extração e catalogação de questões de concursos da Marinha do Brasil (CA-AA-AFN).\n"
            "Abaixo está o texto completo da prova de concurso, com as colunas organizadas de forma sequencial de cima para baixo:\n"
            f"--- TEXTO COMPLETO DA PROVA ---\n{full_exam_text}\n--- FIM DO TEXTO COMPLETO ---\n\n"
            f"Extraia as questões número {ini} até {fim} contidas no texto acima.\n"
            "Siga rigorosamente as seguintes diretrizes:\n"
            "- Se a questão referenciar um texto base longo de apoio (poemas, charges, tabelas de apoio) presente em páginas anteriores, localize-o no texto completo da prova, extraia-o na íntegra e preencha no campo 'texto_base'.\n"
            "- Use LaTeX para expressões e fórmulas matemáticas, se existirem (ex: $x^2 + y^2 = 25$ ou $$\\cos(2x)$$).\n"
            "  ATENÇÃO: Toda barra invertida (`\\`) do LaTeX deve ser escapada como barra dupla (`\\\\`) na string JSON resultante para evitar erros de parser (ex: `\\\\cos` em vez de `\\cos`).\n"
            "- Classifique a disciplina de cada questão:\n"
            "  * Q1-8: 'Língua Portuguesa'\n"
            "  * Q9-15: 'Matemática'\n"
            "  * Q16-23: 'Geografia'\n"
            "  * Q24-30: 'História'\n"
            "  * Q31-50: 'Conhecimento Militar-Naval'\n"
            "- Identifique o assunto e o subassunto de forma coerente e concisa.\n"
            "- Gere tags relevantes (em minúsculas).\n"
            "- Classifique a dificuldade como 'facil', 'media' ou 'dificil'.\n"
            "- Forneça uma explicação detalhada no campo 'comentario_gabarito'.\n"
            "- Retorne a resposta no formato JSON em conformidade com o esquema fornecido."
        )
        
        success = False
        response_text = ""
        # Call Gemini (3.5 Flash)
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=QuestionListModel,
                        temperature=0.1,
                        max_output_tokens=8192
                    )
                )
                response_text = response.text
                if response.candidates[0].finish_reason == types.FinishReason.STOP:
                    success = True
                    break
                else:
                    print(f"    Warning: Chunk {ini}-{fim} output was truncated ({response.candidates[0].finish_reason}). Retrying...")
            except Exception as e:
                print(f"    Error on attempt {attempt+1} for chunk {ini}-{fim}: {e}")
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "quota" in str(e).lower():
                    print("    Detected API Rate Limit/Quota exhaustion. Enabling fallback mode.")
                    API_QUOTA_EXHAUSTED = True
                    break
                print("    Retrying in 4s...")
                time.sleep(4)
                
        # Parse chunk JSON
        parsed_chunk = False
        if success and not API_QUOTA_EXHAUSTED:
            try:
                cleaned_response = fix_json_backslashes(response_text)
                data = json.loads(cleaned_response)
                extracted_qs = data.get("questoes", [])
                
                # Check that we got all questions in range
                nums_extracted = [q["num"] for q in extracted_qs]
                expected_nums = list(range(ini, fim + 1))
                if all(n in nums_extracted for n in expected_nums):
                    print(f"    Successfully extracted {len(extracted_qs)} questions (Numbers: {nums_extracted})")
                    cache[cache_key] = extracted_qs
                    save_cache(cache)
                    questoes_ano.extend(extracted_qs)
                    parsed_chunk = True
                else:
                    print(f"    Warning: Some questions were missing in chunk output. Expected {expected_nums}, got {nums_extracted}.")
            except Exception as e:
                print(f"    Warning: JSON parse failed for chunk: {e}")
                
        # Fallback recursive strategy: extract one-by-one
        if not parsed_chunk:
            print(f"    [FALLBACK] Failed to process chunk Q{ini}-Q{fim} together. Processing questions one by one...")
            chunk_qs = []
            for num in range(ini, fim + 1):
                single_cache_key = f"{ano}_{num}"
                if single_cache_key in cache:
                    print(f"      [CACHE] Using cached extraction for Q{num} of year {ano}")
                    chunk_qs.append(cache[single_cache_key])
                    continue
                    
                if API_QUOTA_EXHAUSTED:
                    print(f"      [QUOTA EXHAUSTED] Directly using local fallback for Q{num}...")
                    raw_q_text = extract_raw_question_text(full_exam_text, num)
                    q_parsed = extract_question_fallback(ano, num, raw_q_text)
                    cache[single_cache_key] = q_parsed
                    save_cache(cache)
                    chunk_qs.append(q_parsed)
                    continue
                    
                print(f"      Extracting Q{num}...")
                raw_q_text = extract_raw_question_text(full_exam_text, num)
                
                single_prompt = (
                    "Você é um especialista em extração e catalogação de questões de concursos da Marinha do Brasil (CA-AA-AFN).\n"
                    "Abaixo está o texto contendo a questão:\n"
                    f"--- QUESTÃO RAW ---\n{raw_q_text}\n--- FIM ---\n\n"
                    f"Extraia a questão número {num} no formato JSON de acordo com o esquema fornecido.\n"
                    "Siga rigorosamente as seguintes diretrizes:\n"
                    "- Use LaTeX para expressões e fórmulas matemáticas, se existirem (ex: $x^2 + y^2 = 25$ ou $$\\cos(2x)$$).\n"
                    "  ATENÇÃO: Toda barra invertida (`\\`) do LaTeX deve ser escapada como barra dupla (`\\\\`) na string JSON resultante para evitar erros de parser (ex: `\\\\cos` in vez de `\\cos`).\n"
                    "- Identifique o assunto e o subassunto.\n"
                    "- Gere tags relevantes (em minúsculas).\n"
                    "- Classifique a dificuldade como 'facil', 'media' ou 'dificil'.\n"
                    "- Forneça uma explicação detalhada no campo 'comentario_gabarito'.\n"
                    "Retorne a resposta no formato JSON em conformidade com o esquema fornecido."
                )
                
                single_success = False
                single_response_text = ""
                for attempt in range(2):
                    try:
                        response = client.models.generate_content(
                            model='gemini-3.5-flash',
                            contents=single_prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=QuestionModel,
                                temperature=0.1,
                                max_output_tokens=2048
                            )
                        )
                        single_response_text = response.text
                        if response.candidates[0].finish_reason == types.FinishReason.STOP:
                            single_success = True
                            break
                    except Exception as e:
                        print(f"      Error on single Q{num} attempt {attempt+1}: {e}")
                        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "quota" in str(e).lower():
                            print("      Detected API Rate Limit/Quota exhaustion. Enabling fallback mode.")
                            API_QUOTA_EXHAUSTED = True
                            break
                        time.sleep(3)
                        
                q_parsed = None
                if single_success and not API_QUOTA_EXHAUSTED:
                    try:
                        cleaned_single = fix_json_backslashes(single_response_text)
                        q_parsed = json.loads(cleaned_single)
                    except Exception as e:
                        print(f"      Warning: JSON parse failed for Q{num}: {e}")
                        
                if q_parsed:
                    print(f"      Successfully extracted Q{num} via Gemini.")
                else:
                    print(f"      [FALLBACK] Failed to extract Q{num} via Gemini. Using local python fallback.")
                    q_parsed = extract_question_fallback(ano, num, raw_q_text)
                    
                cache[single_cache_key] = q_parsed
                save_cache(cache)
                chunk_qs.append(q_parsed)
                if not API_QUOTA_EXHAUSTED:
                    time.sleep(4)
                
            # Add single questions of this failed chunk to main list
            questoes_ano.extend(chunk_qs)
            
        if not API_QUOTA_EXHAUSTED:
            time.sleep(4)
        
    # Post-process questions: inject answers, IDs, and compatibility fields
    final_questoes = []
    for q in questoes_ano:
        num = q["num"]
        
        # Map IDs and colors
        id_global = f"CAAAFN_{ano}_{spec['prova'][0]}_{num:03d}"  # E.g. CAAAFN_2019_V_001
        compat_id = f"q{ano}-{num:02d}"  # E.g. q2019-01
        
        # Get subject mapping
        disciplina, materia, materiaNome = get_subject_info(num)
        
        # Get answers from gabarito
        gab_info = gabaritos.get(num, {"gabarito": "", "anulada": False})
        
        final_q = {
            "id_global": id_global,
            "id": compat_id,  # Compatibility field
            "num": num,
            "ano": ano,
            "prova": spec["prova"],
            "candidatos": spec["candidatos"],
            "disciplina": q.get("disciplina", disciplina),
            "materia": materia,  # Compatibility field
            "materiaNome": materiaNome,  # Compatibility field
            "assunto": q.get("assunto", "Geral").strip(),
            "subassunto": q.get("subassunto", "Geral").strip(),
            "nivel": "concurso",
            "enunciado": clean_extracted_text(q.get("enunciado", "")).strip(),
            "alternativas": {k: clean_extracted_text(v).strip() for k, v in q.get("alternativas", {}).items()},
            "gabarito": gab_info["gabarito"],
            "anulada": gab_info["anulada"],
            "dificuldade": q.get("dificuldade", "media").strip().lower(),
            "comentario_gabarito": q.get("comentario_gabarito", "").strip(),
            "texto_base": q.get("texto_base", "").strip(),
            "tags": [t.lower().strip() for t in q.get("tags", [])],
            "fonte": f"{ano}.pdf"
        }
        final_questoes.append(final_q)
        
    return final_questoes

# Main Runner
def main():
    # Setup GenAI client
    client = genai.Client(api_key=API_KEY)
    
    # Load existing cache
    cache = load_cache()
    
    todas_questoes = []
    
    # Process each year
    for ano in sorted(EXAM_SPECS.keys()):
        exam_pdf = os.path.join(BASE_DIR, str(ano), f"{ano}.pdf")
        gabarito_pdf = os.path.join(BASE_DIR, str(ano), "gabarito.pdf")
        
        if not os.path.exists(exam_pdf) or not os.path.exists(gabarito_pdf):
            print(f"Skipping year {ano}: Files not found under {BASE_DIR}/{ano}")
            continue
            
        # Parse answer key
        gabarito_map = parse_gabarito(ano, gabarito_pdf)
        
        # Process exam questions
        questoes_ano = extrair_prova_ano(client, cache, ano, exam_pdf, gabarito_map)
        todas_questoes.extend(questoes_ano)
        
    if not todas_questoes:
        print("No questions processed. Exiting.")
        return
        
    # Sort questions by year and number
    todas_questoes.sort(key=lambda x: (x["ano"], x["num"]))
    
    # Output Consolidated JSON
    print(f"\nSaving consolidated JSON to {OUTPUT_CONSOLIDADO}...")
    banco_consolidado = {
        "meta": {
            "concurso": "CA-AA-AFN",
            "versao": "1.0",
            "geradoEm": datetime.datetime.now().isoformat(),
            "total": len(todas_questoes)
        },
        "questoes": todas_questoes
    }
    
    with open(OUTPUT_CONSOLIDADO, "w", encoding="utf-8") as f:
        json.dump(banco_consolidado, f, ensure_ascii=False, indent=2)
        
    # Also save to Estudos root directory for immediate UI updates
    app_banco_questoes = os.path.join(r"C:\Users\alexa\Documents\Estudos", "banco_questoes.json")
    print(f"Copying bank to study app root: {app_banco_questoes}")
    with open(app_banco_questoes, "w", encoding="utf-8") as f:
        json.dump(banco_consolidado, f, ensure_ascii=False, indent=2)

    # Generate Subject JSONs
    subject_files = {
        "portugues": "portugues.json",
        "matematica": "matematica.json",
        "geografia": "geografia.json",
        "historia": "historia.json"
    }
    
    for mat_code, filename in subject_files.items():
        sub_qs = [q for q in todas_questoes if q["materia"] == mat_code]
        sub_path = os.path.join(BASE_DIR, filename)
        print(f"Saving subject bank '{mat_code}' to {sub_path}...")
        with open(sub_path, "w", encoding="utf-8") as f:
            json.dump({
                "meta": {
                    "concurso": "CA-AA-AFN",
                    "disciplina": mat_code,
                    "total": len(sub_qs)
                },
                "questoes": sub_qs
            }, f, ensure_ascii=False, indent=2)
            
    # Generate Statistics JSON
    print("Generating statistics...")
    stats = {}
    for q in todas_questoes:
        disciplina = q["disciplina"]
        assunto = q["assunto"] or "Sem Assunto"
        
        if disciplina not in stats:
            stats[disciplina] = {}
        if assunto not in stats[disciplina]:
            stats[disciplina][assunto] = 0
        stats[disciplina][assunto] += 1
        
    with open(OUTPUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
        
    # Generate Simulados
    print("Generating simulados...")
    simulado_facil = [q for q in todas_questoes if q["dificuldade"] == "facil"]
    simulado_medio = [q for q in todas_questoes if q["dificuldade"] == "media"]
    simulado_dificil = [q for q in todas_questoes if q["dificuldade"] == "dificil"]
    simulado_real = [q for q in todas_questoes if q["ano"] == 2024]
    
    simulados = {
        "simulado_facil.json": simulado_facil,
        "simulado_medio.json": simulado_medio,
        "simulado_dificil.json": simulado_dificil,
        "simulado_estilo_prova_real.json": simulado_real
    }
    
    for filename, qs in simulados.items():
        sim_path = os.path.join(BASE_DIR, filename)
        print(f"Saving simulado '{filename}' ({len(qs)} questions) to {sim_path}...")
        with open(sim_path, "w", encoding="utf-8") as f:
            json.dump({
                "meta": {
                    "concurso": "CA-AA-AFN",
                    "tipo": filename.replace(".json", ""),
                    "total": len(qs)
                },
                "questoes": qs
            }, f, ensure_ascii=False, indent=2)
            
    print(f"\n==========================================")
    print(f"SUCCESS: Pipeline finished successfully!")
    print(f"Total Questions: {len(todas_questoes)}")
    print(f"==========================================")

if __name__ == '__main__':
    main()
