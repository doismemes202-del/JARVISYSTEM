"""PLATO — integração Google Sheets (gspread) para listas de materiais (BOM).

Pré-requisito: uma Service Account do Google Cloud com Sheets API + Drive API ativas.
  - Salve a chave JSON em credentials/service_account.json (ou GOOGLE_CREDENTIALS no .env)
  - Compartilhe a planilha com o e-mail da service account (Editor), e informe BOM_SPREADSHEET_ID,
    ou deixe vazio para o sistema criar "JARVIS - BOM" e compartilhar com GOOGLE_SHARE_EMAIL.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from . import config

log = logging.getLogger("jarvis.plato")

HEADERS = ["Item", "Componente", "Especificação", "Qtd", "Unidade",
           "Preço Unit. (R$)", "Subtotal (R$)", "Fornecedor", "Link", "Observações"]


class SheetsNotConfigured(RuntimeError):
    pass


# ---------------------------------------------------------------- parsing
def _num(v, default=0.0) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v or ""))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def parse_bom_json(text: str) -> list[dict]:
    """Aceita {"itens":[...]} ou [...] (inclusive cercado de texto/markdown)."""
    text = (text or "").strip()
    data = None
    for candidate in (text, *re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)):
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, TypeError):
            continue
    if data is None:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not m:
            raise ValueError("BOM sem JSON reconhecível.")
        data = json.loads(m.group(1))

    if isinstance(data, dict):
        data = data.get("itens") or data.get("items") or data.get("bom") or next(
            (v for v in data.values() if isinstance(v, list)), [])
    items = []
    for raw in data or []:
        if not isinstance(raw, dict):
            continue
        items.append({
            "componente": str(raw.get("componente") or raw.get("item") or raw.get("nome") or "?"),
            "especificacao": str(raw.get("especificacao") or raw.get("especificação") or raw.get("spec") or ""),
            "qtd": _num(raw.get("qtd") or raw.get("quantidade"), 1),
            "unidade": str(raw.get("unidade") or "un"),
            "preco_unit": _num(raw.get("preco_unit") or raw.get("preco") or raw.get("preço")),
            "fornecedor": str(raw.get("fornecedor") or ""),
            "link": str(raw.get("link") or ""),
            "obs": str(raw.get("obs") or raw.get("observacoes") or ""),
        })
    if not items:
        raise ValueError("BOM vazia.")
    return items


def bom_total(items: list[dict]) -> float:
    return round(sum(i["qtd"] * i["preco_unit"] for i in items), 2)


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_bom_table(items: list[dict]) -> str:
    lines = [f"{'#':>2}  {'Componente':<28} {'Qtd':>6} {'Un':<4} {'Unit.':>12} {'Subtotal':>13}"]
    for n, i in enumerate(items, 1):
        lines.append(f"{n:>2}  {i['componente'][:28]:<28} {i['qtd']:>6g} {i['unidade'][:4]:<4} "
                     f"{brl(i['preco_unit']):>12} {brl(i['qtd'] * i['preco_unit']):>13}")
    lines.append(f"{'':>2}  {'TOTAL':<28} {'':>6} {'':<4} {'':>12} {brl(bom_total(items)):>13}")
    return "\n".join(lines)


# ---------------------------------------------------------------- gspread
def _client():
    if not config.GOOGLE_CREDENTIALS.exists():
        raise SheetsNotConfigured(
            f"Credencial não encontrada em {config.GOOGLE_CREDENTIALS}. "
            "Crie uma Service Account e salve o JSON nesse caminho.")
    import gspread
    return gspread.service_account(filename=str(config.GOOGLE_CREDENTIALS))


def _spreadsheet(gc):
    import gspread
    if config.BOM_SPREADSHEET_ID:
        return gc.open_by_key(config.BOM_SPREADSHEET_ID)
    try:
        return gc.open(config.BOM_SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        sh = gc.create(config.BOM_SPREADSHEET_NAME)
        if config.GOOGLE_SHARE_EMAIL:
            sh.share(config.GOOGLE_SHARE_EMAIL, perm_type="user", role="writer")
        log.info("Planilha '%s' criada: %s", config.BOM_SPREADSHEET_NAME, sh.url)
        return sh


def _safe_title(name: str) -> str:
    title = re.sub(r"[\[\]\*\?/\\:]", "-", name).strip()[:90]
    return title or f"Projeto {datetime.now():%Y-%m-%d %H%M}"


def create_project_tab(project: str):
    """Cria (ou reaproveita) a aba do projeto com cabeçalho formatado."""
    import gspread
    sh = _spreadsheet(_client())
    title = _safe_title(project)
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=300, cols=len(HEADERS))
        log.info("Aba '%s' criada.", title)
    ws.update(range_name="A1", values=[HEADERS])
    try:
        ws.format("A1:J1", {"textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.05, "green": 0.2, "blue": 0.25}})
        ws.freeze(rows=1)
    except Exception:
        pass
    return sh, ws


def save_bom(project: str, items: list[dict]) -> str:
    """Grava a BOM cotada na aba do projeto. Retorna a URL da aba."""
    sh, ws = create_project_tab(project)
    ws.batch_clear(["A2:J300"])
    rows = []
    for n, i in enumerate(items, 1):
        r = n + 1
        rows.append([n, i["componente"], i["especificacao"], i["qtd"], i["unidade"],
                     i["preco_unit"], f"=D{r}*F{r}", i["fornecedor"], i["link"], i["obs"]])
    last = len(items) + 1
    rows.append(["", "TOTAL", "", "", "", "", f"=SUM(G2:G{last})", "",
                 f"Gerado pelo JARVIS em {datetime.now():%d/%m/%Y %H:%M}", ""])
    ws.update(range_name="A2", values=rows, value_input_option="USER_ENTERED")
    try:
        ws.format(f"F2:G{last + 1}", {"numberFormat": {"type": "CURRENCY", "pattern": "R$ #,##0.00"}})
        ws.format(f"A{last + 1}:J{last + 1}", {"textFormat": {"bold": True}})
    except Exception:
        pass
    url = f"{sh.url}#gid={ws.id}"
    log.info("BOM '%s' salva (%d itens): %s", project, len(items), url)
    return url
