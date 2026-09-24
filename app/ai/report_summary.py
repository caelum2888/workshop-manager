"""Consolidação textual opcional com IA para o relatório mensal.

Única parte do sistema que usa LLM, e só para redigir texto a partir de
registros existentes. Nunca calcula nada. Sem LLM_API_KEY (ou em caso de
erro), devolve os registros originais organizados por data. O resultado é
sempre uma *sugestão*: a pessoa revisa e edita antes de salvar.
"""

import logging
from dataclasses import asdict, dataclass

from app import config

log = logging.getLogger(__name__)

# Modelos que aceitam o fallback de recusa do lado do servidor.
SERVER_FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}

FIELDS = {
    "activities_summary": {
        "title": "Resumo das Atividades Realizadas",
        "instruction": (
            "Redija um resumo do que foi trabalhado em sala de aula durante o mês, "
            "agrupando conteúdos relacionados em vez de listar aula por aula."
        ),
    },
    "highlights_occurrences": {
        "title": "Destaques e Ocorrências",
        "instruction": (
            "Consolide os destaques e as ocorrências registrados, mantendo as datas "
            "e os fatos que a coordenação precisa saber."
        ),
    },
    "methodology_notes": {
        "title": "Metodologia Aplicada e Resultados Observados — Observações",
        "instruction": (
            "Consolide as observações sobre metodologia e reação dos alunos que "
            "foram registradas pelo professor."
        ),
    },
}

SYSTEM_PROMPT = """Você ajuda professores do projeto Oficinas Itinerantes a redigir o relatório mensal de atividades.
Você recebe registros escritos durante as aulas e devolve um texto consolidado, em português do Brasil, com linguagem profissional, clara e simples.

O relatório é um documento administrativo, então a fidelidade aos registros é mais importante que a fluidez:
- Use apenas o que está escrito nos registros. Não acrescente atividades, avaliações, resultados ou fatos.
- Não afirme que alunos evoluíram, se engajaram ou atingiram objetivos, a menos que o registro diga isso.
- Não cite números de presença ou estatísticas; o sistema calcula esses dados em outra seção.
- Se os registros forem curtos ou vagos, seja breve também, sem preencher lacunas.

Responda somente com o texto final, sem título, sem markdown e sem comentários sobre a tarefa, em até três parágrafos curtos."""


@dataclass
class TextSuggestion:
    text: str
    source: str  # "ai" ou "records"
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_ai_configured() -> bool:
    return bool(config.LLM_API_KEY)


def format_records(entries: list[dict]) -> str:
    """Consolidação sem IA: um item por registro, em ordem cronológica."""
    lines = []
    for entry in entries:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        prefix = entry["date"].strftime("%d/%m")
        if entry.get("class_name"):
            prefix += f" ({entry['class_name']})"
        lines.append(f"• {prefix}: {text}")
    return "\n".join(lines)


def suggest_text(field: str, entries: list[dict], context: dict, use_ai: bool = True) -> TextSuggestion:
    """Sugere o texto de uma seção do relatório a partir dos registros das aulas.

    entries: [{"date": date, "class_name": str, "text": str}, ...]
    context: {"workshop_name": str, "period_label": str}
    """
    records_text = format_records(entries)
    if not records_text:
        return TextSuggestion("", "records", "Não há registros para esta seção no mês.")
    if not use_ai:
        return TextSuggestion(records_text, "records")
    if not is_ai_configured():
        return TextSuggestion(
            records_text, "records", "IA não configurada (LLM_API_KEY). Exibindo os registros originais."
        )
    try:
        text = _call_llm(FIELDS[field], records_text, context)
    except Exception as exc:  # qualquer falha de IA cai para os registros originais
        log.warning("Falha na consolidação com IA: %s", exc)
        return TextSuggestion(records_text, "records", f"{_error_message(exc)} Exibindo os registros originais.")
    return TextSuggestion(text, "ai", "Texto sugerido pela IA a partir dos registros. Revise antes de finalizar.")


def _error_message(exc: Exception) -> str:
    # A IA é opcional. O próprio tratamento de uma falha não pode depender de
    # o SDK opcional estar instalado, senão o fallback para os registros também
    # falha justamente nas instalações básicas do MVP.
    try:
        import anthropic
    except ImportError:
        return "Não foi possível usar a IA."

    if isinstance(exc, anthropic.AuthenticationError):
        return "Chave de IA inválida."
    if isinstance(exc, anthropic.RateLimitError):
        return "Limite de uso da IA atingido; tente mais tarde."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Serviço de IA respondeu com erro {exc.status_code}."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Sem conexão com o serviço de IA."
    return "Não foi possível usar a IA."


def _call_llm(field_cfg: dict, records_text: str, context: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.LLM_API_KEY, timeout=120.0)
    prompt = (
        f"Seção do relatório: {field_cfg['title']}\n"
        f"Oficina: {context.get('workshop_name', '')}\n"
        f"Mês de referência: {context.get('period_label', '')}\n\n"
        f"Tarefa: {field_cfg['instruction']}\n\n"
        f"<registros>\n{records_text}\n</registros>"
    )
    params = {
        "model": config.LLM_MODEL,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.LLM_MODEL in SERVER_FALLBACK_MODELS:
        response = client.beta.messages.create(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        response = client.messages.create(**params)

    if response.stop_reason == "refusal":
        raise RuntimeError("O modelo recusou a solicitação.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("Resposta vazia do modelo.")
    return text
