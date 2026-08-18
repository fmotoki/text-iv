from __future__ import annotations


LEGACY_RECURSIVE_PROMPT_VERSION = "forecaster_recursive_v1"
COMMON_PRIOR_PROMPT_VERSION = "forecaster_common_prior_v1"
DEFAULT_FORECASTER_PROMPT_VERSION = LEGACY_RECURSIVE_PROMPT_VERSION


COMMON_PRIOR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "nova_expectativa_inflacao": {"type": "number", "minimum": 0, "maximum": 30},
    },
    "required": ["nova_expectativa_inflacao"],
    "additionalProperties": False,
}


def normalize_forecaster_prompt_version(prompt_version: str | None = None) -> str:
    version = (prompt_version or DEFAULT_FORECASTER_PROMPT_VERSION).strip().lower()
    aliases = {
        "legacy": LEGACY_RECURSIVE_PROMPT_VERSION,
        "recursive": LEGACY_RECURSIVE_PROMPT_VERSION,
        "recursive_v1": LEGACY_RECURSIVE_PROMPT_VERSION,
        "common_prior": COMMON_PRIOR_PROMPT_VERSION,
        "common_prior_v1": COMMON_PRIOR_PROMPT_VERSION,
    }
    version = aliases.get(version, version)
    if version not in {LEGACY_RECURSIVE_PROMPT_VERSION, COMMON_PRIOR_PROMPT_VERSION}:
        raise ValueError(f"Unknown forecaster prompt version {prompt_version!r}")
    return version


def build_forecaster_prompt(
    forecaster_type: str,
    previous_inflation: float,
    target: float,
    prompt_version: str | None = None,
) -> str:
    """Build the system prompt used by synthetic Focus forecasters.

    This function centralizes the prompt family that was previously repeated
    across legacy scripts. It does not call any model.
    """
    if forecaster_type not in {"anchored", "unanchored"}:
        raise ValueError("forecaster_type must be 'anchored' or 'unanchored'")

    version = normalize_forecaster_prompt_version(prompt_version)
    if version == COMMON_PRIOR_PROMPT_VERSION:
        return build_common_prior_forecaster_prompt(
            forecaster_type=forecaster_type,
            previous_inflation=previous_inflation,
            target=target,
        )

    if forecaster_type == "anchored":
        credibility_block = (
            "Voce acredita que o regime de metas de inflacao no Brasil e, em geral, levado a serio "
            "pelo Banco Central. A meta oficial funciona como uma ancora de medio prazo."
        )
    else:
        credibility_block = (
            "Voce considera que a meta de inflacao pode perder forca como ancora quando a comunicacao "
            "sinaliza riscos persistentes, dominancia fiscal ou tolerancia com desvios."
        )

    return f"""
Voce e um economista responsavel por projecoes de inflacao em uma instituicao financeira brasileira.
Voce participa regularmente da Pesquisa Focus e informa a projecao de IPCA acumulado nos proximos 12 meses.

{credibility_block}

Memoria:
Na reuniao anterior, sua projecao para IPCA 12m era de {previous_inflation:.2f}%.
A meta oficial de inflacao relevante e {target:.2f}% ao ano.

Tarefa:
1. Leia cuidadosamente a nova Ata do Copom.
2. Avalie o tom de politica monetaria em escala de 1 a 7, onde 1 e muito dovish, 4 e neutro, e 7 e muito hawkish.
3. Atualize sua projecao de IPCA 12m, partindo da projecao anterior e das novas informacoes da Ata.

Responda apenas com JSON valido:
{{
  "percepcao_tom_score": int,
  "nova_expectativa_inflacao": float
}}
""".strip()


def build_common_prior_forecaster_prompt(
    forecaster_type: str,
    previous_inflation: float,
    target: float,
) -> str:
    """Build the frozen forecast-only prompt for the common-prior design."""
    if forecaster_type not in {"anchored", "unanchored"}:
        raise ValueError("forecaster_type must be 'anchored' or 'unanchored'")

    if forecaster_type == "anchored":
        belief_block = (
            "Voce atribui alta credibilidade a meta oficial como ancora de medio prazo. "
            "Na ausencia de evidencia clara no texto fornecido, voce evita revisoes grandes e "
            "mantem a projecao proxima do estado anterior e da meta. Essa descricao define apenas "
            "a precisao de sua crenca anterior; ela nao determina a direcao da revisao."
        )
    else:
        belief_block = (
            "Voce atribui menor credibilidade a meta oficial como ancora de medio prazo. "
            "Voce permite revisoes maiores quando o texto fornecido altera sua avaliacao dos riscos "
            "para a inflacao. Essa descricao define apenas a precisao de sua crenca anterior; ela "
            "nao determina a direcao da revisao."
        )

    return f"""
Voce e um economista responsavel por projecoes de inflacao em uma instituicao financeira brasileira.
Voce participa regularmente da Pesquisa Focus e informa a projecao do IPCA acumulado nos proximos 12 meses.

ESTADO PRE-TRATAMENTO FIXADO PELO EXPERIMENTO
- Sua projecao imediatamente antes de ler a comunicacao atual e {previous_inflation:.2f}%.
- A meta oficial de inflacao relevante e {target:.2f}% ao ano.
- A projecao anterior foi definida antes da comunicacao atual e deve ser aceita como dada.

PERFIL DE CRENCA
{belief_block}

REGRAS DE INFORMACAO
1. Use exclusivamente a projecao anterior, a meta e o texto fornecidos nesta solicitacao.
2. Nao use memoria sobre a reuniao, a ata, a Pesquisa Focus ou resultados historicos reais.
3. Se reconhecer o episodio, ignore qualquer informacao lembrada que nao esteja no texto fornecido.
4. Nao suponha que o texto pertence a um tratamento, cenario ou versao especifica.
5. Atualize a projecao somente na medida em que a comunicacao fornecida alterar sua avaliacao da inflacao nos proximos 12 meses.
6. Se o texto nao justificar uma revisao, mantenha a projecao anterior.

TAREFA
Depois de ler a comunicacao, informe sua nova projecao de IPCA acumulado nos proximos 12 meses.
Use um numero entre 0 e 30, em pontos percentuais. Informe somente a projecao, sem justificativa,
rotulos ou avaliacoes adicionais.

Responda apenas com JSON valido, sem markdown e sem campos adicionais:
{{
  "nova_expectativa_inflacao": number
}}
""".strip()


def forecaster_response_schema(prompt_version: str | None = None) -> dict | None:
    version = normalize_forecaster_prompt_version(prompt_version)
    if version == COMMON_PRIOR_PROMPT_VERSION:
        return COMMON_PRIOR_RESPONSE_SCHEMA
    return None
