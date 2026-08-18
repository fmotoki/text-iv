from __future__ import annotations


PROMPT_VERSION = "production_v0.2"
DEFAULT_PROMPT_VERSION = PROMPT_VERSION
LEGACY_SERIES_PROMPT_VERSION = "legacy_series_v1"
CALIBRATED_CONTRAST_PROMPT_VERSION = "calibrated_contrast_v1"
CALIBRATED_CONTRAST_V2_PROMPT_VERSION = "calibrated_contrast_v2"
CALIBRATED_CONTRAST_V3_PROMPT_VERSION = "calibrated_contrast_v3"
CALIBRATED_LENGTH_PROMPT_VERSION = "calibrated_length_v1"
CALIBRATED_REGISTER_PROMPT_VERSION = "calibrated_register_v1"
CALIBRATED_DOVE_REBALANCE_PROMPT_VERSION = "calibrated_dove_rebalance_v1"
CALIBRATED_DOVE_REBALANCE_V2_PROMPT_VERSION = "calibrated_dove_rebalance_v2"
CALIBRATED_SYMMETRIC_REBALANCE_PROMPT_VERSION = "calibrated_symmetric_rebalance_v1"
CALIBRATED_SYMMETRIC_REBALANCE_V2_PROMPT_VERSION = "calibrated_symmetric_rebalance_v2"
CALIBRATED_SYMMETRIC_REBALANCE_V3_PROMPT_VERSION = "calibrated_symmetric_rebalance_v3"
CALIBRATED_SYMMETRIC_REBALANCE_V4_PROMPT_VERSION = "calibrated_symmetric_rebalance_v4"
CALIBRATED_SYMMETRIC_REBALANCE_V5_PROMPT_VERSION = "calibrated_symmetric_rebalance_v5"
CALIBRATED_SYMMETRIC_REBALANCE_V6_PROMPT_VERSION = "calibrated_symmetric_rebalance_v6"
CALIBRATED_SYMMETRIC_REBALANCE_V7_PROMPT_VERSION = "calibrated_symmetric_rebalance_v7"
CALIBRATED_SYMMETRIC_REBALANCE_V8_PROMPT_VERSION = "calibrated_symmetric_rebalance_v8"
CALIBRATED_SYMMETRIC_REBALANCE_V9_PROMPT_VERSION = "calibrated_symmetric_rebalance_v9"
CALIBRATED_SYMMETRIC_REBALANCE_V10_PROMPT_VERSION = "calibrated_symmetric_rebalance_v10"


BASE_REWRITE_PROMPT = """Voce recebera um trecho de ata do Comite de Politica Monetaria.
Reescreva o trecho como uma versao contrafactual de estilo, preservando integralmente o conteudo factual.

Regras:
1. Nao adicione fatos, eventos, causas, interpretacoes economicas ou decisoes que nao estejam no texto original.
2. Nao remova fatos relevantes do texto original.
3. Nao altere, crie, remova ou converta numeros, datas, percentuais, sinais, unidades, moedas ou casas decimais.
4. Preserve entidades, decisoes, direcao causal, sequencia dos topicos, numero de paragrafos nao vazios e qualquer titulo/cabecalho inicial.
5. Mantenha comprimento proximo ao original.
6. Entregue somente o texto final reescrito, sem comentarios ou explicacoes. Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista ou marcacao que ja exista no original.
7. Se a mudanca de estilo exigir alterar fatos, preserve os fatos e faca apenas a mudanca estilistica possivel.
"""


STANCE_INSTRUCTIONS = {
    "hawk": (
        "Use uma redacao institucional mais cautelosa e menos complacente diante "
        "do mesmo cenario. Quando houver margem retorica, prefira formulacoes "
        "que transmitam avaliacao mais severa das condicoes descritas e menor "
        "conforto com desvios em relacao aos objetivos do Comite, sem inserir "
        "fundamentos novos."
    ),
    "dove": (
        "Use uma redacao institucional mais serena e menos alarmada diante do "
        "mesmo cenario. Quando houver margem retorica, prefira formulacoes que "
        "transmitam avaliacao menos severa das condicoes descritas e maior "
        "conforto com a evolucao esperada, sem inserir fundamentos novos."
    ),
    "neutral": (
        "Use tom neutro e tecnico. Preserve o conteudo e reduza escolhas retoricas "
        "que enfatizem direcao hawkish ou dovish."
    ),
}


def build_rewrite_prompt(stance: str) -> str:
    return build_rewrite_prompt_versioned(stance, prompt_version=DEFAULT_PROMPT_VERSION)


def normalize_prompt_version(prompt_version: str | None = None) -> str:
    version = (prompt_version or DEFAULT_PROMPT_VERSION).strip()
    if version in {"production", "prod", "v0.2"}:
        return DEFAULT_PROMPT_VERSION
    if version in {"legacy", "original", "legacy_series"}:
        return LEGACY_SERIES_PROMPT_VERSION
    if version in {"calibrated", "contrast", "calibrated_contrast", "v1_contrast"}:
        return CALIBRATED_CONTRAST_PROMPT_VERSION
    if version in {"calibrated_v2", "contrast_v2", "calibrated_contrast_v2", "v2_contrast"}:
        return CALIBRATED_CONTRAST_V2_PROMPT_VERSION
    if version in {"calibrated_v3", "contrast_v3", "calibrated_contrast_v3", "v3_contrast"}:
        return CALIBRATED_CONTRAST_V3_PROMPT_VERSION
    if version in {"calibrated_length", "length_v1", "bounded_length", "calibrated_length_v1"}:
        return CALIBRATED_LENGTH_PROMPT_VERSION
    if version in {"calibrated_register", "register_v1", "old_register", "calibrated_register_v1"}:
        return CALIBRATED_REGISTER_PROMPT_VERSION
    if version in {
        "calibrated_dove_rebalance",
        "dove_rebalance",
        "dove_rebalance_v1",
        "calibrated_dove_rebalance_v1",
    }:
        return CALIBRATED_DOVE_REBALANCE_PROMPT_VERSION
    if version in {"dove_rebalance_v2", "calibrated_dove_rebalance_v2"}:
        return CALIBRATED_DOVE_REBALANCE_V2_PROMPT_VERSION
    if version in {
        "symmetric_rebalance",
        "symmetric_rebalance_v1",
        "calibrated_symmetric_rebalance",
        "calibrated_symmetric_rebalance_v1",
    }:
        return CALIBRATED_SYMMETRIC_REBALANCE_PROMPT_VERSION
    if version in {"symmetric_rebalance_v2", "calibrated_symmetric_rebalance_v2"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V2_PROMPT_VERSION
    if version in {"symmetric_rebalance_v3", "calibrated_symmetric_rebalance_v3"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V3_PROMPT_VERSION
    if version in {"symmetric_rebalance_v4", "calibrated_symmetric_rebalance_v4"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V4_PROMPT_VERSION
    if version in {"symmetric_rebalance_v5", "calibrated_symmetric_rebalance_v5"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V5_PROMPT_VERSION
    if version in {"symmetric_rebalance_v6", "calibrated_symmetric_rebalance_v6"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V6_PROMPT_VERSION
    if version in {"symmetric_rebalance_v7", "calibrated_symmetric_rebalance_v7"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V7_PROMPT_VERSION
    if version in {"symmetric_rebalance_v8", "calibrated_symmetric_rebalance_v8"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V8_PROMPT_VERSION
    if version in {"symmetric_rebalance_v9", "calibrated_symmetric_rebalance_v9"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V9_PROMPT_VERSION
    if version in {"symmetric_rebalance_v10", "calibrated_symmetric_rebalance_v10"}:
        return CALIBRATED_SYMMETRIC_REBALANCE_V10_PROMPT_VERSION
    if version not in {
        DEFAULT_PROMPT_VERSION,
        LEGACY_SERIES_PROMPT_VERSION,
        CALIBRATED_CONTRAST_PROMPT_VERSION,
        CALIBRATED_CONTRAST_V2_PROMPT_VERSION,
        CALIBRATED_CONTRAST_V3_PROMPT_VERSION,
        CALIBRATED_LENGTH_PROMPT_VERSION,
        CALIBRATED_REGISTER_PROMPT_VERSION,
        CALIBRATED_DOVE_REBALANCE_PROMPT_VERSION,
        CALIBRATED_DOVE_REBALANCE_V2_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V2_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V3_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V4_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V5_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V6_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V7_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V8_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V9_PROMPT_VERSION,
        CALIBRATED_SYMMETRIC_REBALANCE_V10_PROMPT_VERSION,
    }:
        raise ValueError(f"Unknown prompt_version {prompt_version!r}")
    return version


def build_rewrite_prompt_versioned(stance: str, prompt_version: str | None = None) -> str:
    version = normalize_prompt_version(prompt_version)
    stance_key = stance.lower()
    if version == LEGACY_SERIES_PROMPT_VERSION:
        return build_legacy_series_rewrite_prompt(stance_key)
    if version == CALIBRATED_CONTRAST_PROMPT_VERSION:
        return build_calibrated_contrast_rewrite_prompt(stance_key)
    if version == CALIBRATED_CONTRAST_V2_PROMPT_VERSION:
        return build_calibrated_contrast_v2_rewrite_prompt(stance_key)
    if version == CALIBRATED_CONTRAST_V3_PROMPT_VERSION:
        return build_calibrated_contrast_v3_rewrite_prompt(stance_key)
    if version == CALIBRATED_LENGTH_PROMPT_VERSION:
        return build_calibrated_length_rewrite_prompt(stance_key)
    if version == CALIBRATED_REGISTER_PROMPT_VERSION:
        return build_calibrated_register_rewrite_prompt(stance_key)
    if version == CALIBRATED_DOVE_REBALANCE_PROMPT_VERSION:
        return build_calibrated_dove_rebalance_rewrite_prompt(stance_key)
    if version == CALIBRATED_DOVE_REBALANCE_V2_PROMPT_VERSION:
        return build_calibrated_dove_rebalance_v2_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V2_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v2_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V3_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v3_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V4_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v4_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V5_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v5_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V6_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v6_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V7_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v7_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V8_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v8_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V9_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v9_rewrite_prompt(stance_key)
    if version == CALIBRATED_SYMMETRIC_REBALANCE_V10_PROMPT_VERSION:
        return build_calibrated_symmetric_rebalance_v10_rewrite_prompt(stance_key)

    try:
        instruction = STANCE_INSTRUCTIONS[stance_key]
    except KeyError as exc:
        raise ValueError(f"Unknown stance {stance!r}") from exc
    return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + instruction


def build_calibrated_contrast_rewrite_prompt(stance: str) -> str:
    """Prompt for a stronger but methodologically bounded tone treatment."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como uma manipulacao experimental de estilo comunicacional. A intervencao deve ser forte o suficiente para que um leitor especializado perceba claramente o deslocamento de tom, mas nao pode mudar fundamentos, decisoes, previsoes, numeros ou encadeamento factual.

REGRAS RIGIDAS
1) CONTEUDO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos ou decisoes que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, ordem dos topicos e qualquer titulo/cabecalho inicial.

2) ESTRUTURA:
   - Preserve o numero de paragrafos nao vazios.
   - Mantenha comprimento proximo ao original; faca substituicoes e reponderacoes retoricas, nao expansoes substantivas.
   - Se uma frase for puramente factual e nao tiver margem retorica, preserve-a e concentre a mudanca nas frases avaliativas do mesmo paragrafo.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

DIMENSOES DE ESTILO A RECALIBRAR EM TODOS OS PARAGRAFOS COM MARGEM RETORICA
- saliencia relativa dos riscos versus conforto com o cenario;
- urgencia institucional versus paciencia;
- rigidez de avaliacao versus gradualismo;
- assimetria percebida dos riscos versus leitura mais balanceada;
- persistencia versus transitoriedade dos choques;
- grau de preocupacao institucional versus confianca institucional;
- custo de desvios da meta versus custo de aperto excessivo sobre atividade e emprego.

CALIBRAGEM DA DOSE
- Em cada paragrafo, faca no minimo 6 mudancas substantivas de estilo quando houver margem retorica.
- As mudancas devem aparecer em verbos modais, adjetivos avaliativos, conectores, enfase de riscos, qualificadores de incerteza e enquadramento do balanco de custos.
- Nao use uma lista fixa de palavras e nao repita mecanicamente as mesmas expressoes.
- Antes de finalizar, verifique internamente que fatos, numeros e estrutura foram preservados e que o deslocamento de tom e claro para um leitor especializado. A resposta final deve conter apenas o texto reescrito.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCAO DE ESTILO - TOM MAIS RIGOROSO:\n"
            + "Recalibre a redacao para uma comunicacao institucional mais severa, vigilante e avessa a riscos inflacionarios. "
            + "Diante dos mesmos fatos, de mais peso retorico a incerteza, persistencia, assimetria altista dos riscos, custo de desvios da meta e necessidade de cautela. "
            + "O texto deve soar menos complacente e mais firme, sem criar recomendacoes, decisoes ou fundamentos novos."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCAO DE ESTILO - TOM MAIS PACIENTE:\n"
            + "Recalibre a redacao para uma comunicacao institucional mais serena, paciente e confiante. "
            + "Diante dos mesmos fatos, de mais peso retorico a defasagens de politica, sinais de acomodacao, transitoriedade dos choques, cautela contra aperto excessivo e efeitos sobre atividade e emprego. "
            + "O texto deve soar menos alarmado e menos restritivo, sem criar recomendacoes, decisoes ou fundamentos novos."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_contrast_v2_rewrite_prompt(stance: str) -> str:
    """High-dose communication-tone treatment with stricter factual bounds."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional em dose alta. O tratamento deve produzir uma diferenca de tom clara para um leitor especializado, mantendo exatamente os mesmos fatos, decisoes, previsoes, condicionantes, numeros e sequencia argumentativa.

REGRAS RIGIDAS
1) CONTEUDO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades e ordem dos topicos.

2) ESTRUTURA:
   - Preserve o numero de paragrafos nao vazios e qualquer titulo/cabecalho inicial.
   - Mantenha comprimento proximo ao original; substitua formulacoes em vez de acrescentar novas informacoes.
   - Frases puramente factuais podem ficar quase iguais. Frases avaliativas, qualificadores e transicoes devem ser recalibrados.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

CONTRASTE FORTE E SIMETRICO
- Em cada paragrafo com material avaliativo, faca no minimo 8 mudancas substantivas de estilo.
- Recalibre verbos modais, adjetivos avaliativos, adverbios de intensidade, conectores, qualificadores de incerteza, enquadramento do balanco de custos e ordem de saliencia das preocupacoes.
- Nao deixe frases avaliativas centrais com o mesmo enquadramento do original.
- O texto deve soar como uma ata plausivel do Copom, sem linguagem teatral, slogans ou recomendacoes novas.
- Nao use uma lista fixa de palavras e nao repita mecanicamente as mesmas expressoes.
- Antes de finalizar, verifique internamente que a estrutura e os fatos foram preservados e que cada paragrafo avaliativo mudou de enquadramento de maneira visivel. A resposta final deve conter apenas o texto reescrito.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCAO DE ESTILO - ENQUADRAMENTO MAIS RIGOROSO:\n"
            + "Reescreva as partes avaliativas para uma comunicacao institucional mais severa, vigilante e avessa a riscos inflacionarios. "
            + "Diante dos mesmos fatos, aumente a saliencia de incerteza, persistencia, assimetria altista dos riscos, custo de desvios da meta e necessidade de cautela. "
            + "Quando o original registrar progresso ou acomodacao, preserve esse fato, mas apresente-o com ressalvas e menor conforto institucional. "
            + "Quando o original registrar riscos ou pressao inflacionaria, preserve a informacao e torne o enquadramento mais firme, sem criar fundamentos ou decisoes."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCAO DE ESTILO - ENQUADRAMENTO MAIS PACIENTE:\n"
            + "Reescreva as partes avaliativas para uma comunicacao institucional mais serena, gradualista e confiante. "
            + "Diante dos mesmos fatos, aumente a saliencia de defasagens da politica, sinais de acomodacao, transitoriedade dos choques, convergencia, balanco de riscos e cautela contra aperto excessivo. "
            + "Quando termos de risco, preocupacao ou pressao forem factuais e precisarem permanecer, preserve-os, mas subordine sua forca com qualificadores de monitoramento, moderacao, gradualismo ou balanco. "
            + "O texto deve reduzir urgencia e severidade sem negar fatos, sem trocar sinais e sem criar fundamentos ou decisoes."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_contrast_v3_rewrite_prompt(stance: str) -> str:
    """High-contrast prompt that targets evaluative clauses, not scorer terms."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional de alta intensidade. A espinha factual deve permanecer igual; a parte avaliativa da comunicacao deve mudar claramente.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E COMPRIMENTO:
   - Preserve o numero de paragrafos nao vazios.
   - Mantenha comprimento proximo ao original; prefira substituicoes a expansoes.
   - Frases puramente factuais podem ficar semelhantes. Frases avaliativas nao devem permanecer com o mesmo enquadramento.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

ALVO DO TRATAMENTO
- O texto final deve representar uma cauda plausivel da comunicacao do Copom: claramente mais rigorosa ou claramente mais paciente, sem mudar fundamentos.
- Em cada paragrafo com qualquer material avaliativo, faca no minimo 7 transformacoes de estilo.
- Em cada frase avaliativa, altere pelo menos uma dimensao: saliencia de riscos, urgencia, forca modal, confianca, persistencia/transitoriedade, assimetria do balanco de riscos, tolerancia a desvios ou custo relativo de aperto excessivo.
- Nao basta inserir adjetivos; reordene a saliencia e troque o enquadramento das avaliacoes.
- Nao use uma lista fixa de palavras e nao repita mecanicamente as mesmas expressoes.
- O texto deve continuar parecendo uma ata oficial, sem linguagem teatral, slogans ou recomendacoes novas.
- Antes de finalizar, verifique internamente que fatos, numeros e paragrafos foram preservados e que nenhum paragrafo avaliativo ficou neutro. A resposta final deve conter apenas o texto reescrito.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK:\n"
            + "Reescreva para um tom institucional nitidamente mais rigoroso, cauteloso e avesso a riscos inflacionarios. "
            + "Diante dos mesmos fatos, reduza conforto com o cenario, aumente a saliencia de incerteza e persistencia, enfatize riscos assimetricos para cima e torne menor a tolerancia retorica a desvios da meta. "
            + "Quando o original mencionar progresso, acomodacao ou convergencia, preserve o fato, mas apresente-o como ainda insuficiente, condicionado ou sujeito a cautela. "
            + "Quando o original mencionar risco, pressao, expectativas ou inflacao, preserve a informacao e torne o enquadramento mais firme, sem criar fatos, fundamentos ou decisoes."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE:\n"
            + "Reescreva para um tom institucional nitidamente mais paciente, sereno e confiante. "
            + "Diante dos mesmos fatos, aumente conforto com o processo descrito, de mais peso a defasagens, acomodacao, convergencia, transitoriedade e balanco dos custos de aperto excessivo. "
            + "Quando o original mencionar risco, pressao, expectativas ou inflacao, preserve a informacao, mas reduza sua urgencia retorica por meio de monitoramento, qualificacao, gradualismo, simetria do balanco ou sinais de arrefecimento presentes no proprio texto. "
            + "Evite ampliar preocupacao, severidade ou linguagem restritiva alem do que ja esta no original; o enquadramento deve ser menos alarmado sem negar fatos, trocar sinais, criar fundamentos ou decisoes."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_length_rewrite_prompt(stance: str) -> str:
    """Bounded rhetorical expansion calibrated from the original artifact."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional. O tratamento deve mudar claramente o enquadramento retorico, preservando fatos, numeros, decisoes, previsoes, condicionalidades e sequencia argumentativa.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos com avaliacao economica ou comunicacao prospectiva, permita expansao retorica controlada: explique melhor a mesma avaliacao e repondere a saliencia dos mesmos elementos.
   - Alvo de comprimento: se houver margem avaliativa, o texto final pode ficar moderadamente maior que o original; se o trecho for factual, tabelar ou nominal, mantenha praticamente igual.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

CALIBRAGEM DA DOSE
- Em cada paragrafo com material avaliativo, faca mudancas visiveis em qualificadores, conectores, forca modal, saliencia de riscos, leitura de incerteza, balanco de custos e grau de conforto institucional.
- A expansao deve vir de qualificacao retorica do mesmo conteudo, nao de novos fatos.
- Nao use uma lista fixa de palavras e nao repita mecanicamente as mesmas expressoes.
- Antes de finalizar, verifique internamente que fatos, numeros, estrutura e formato foram preservados. A resposta final deve conter apenas o texto reescrito.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK COM EXPANSAO CONTROLADA:\n"
            + "Reescreva para um tom institucional mais rigoroso, vigilante e pouco complacente com riscos inflacionarios. "
            + "Diante dos mesmos fatos, aumente a saliencia de incerteza, persistencia, riscos para a inflacao, custo de desvios da meta, necessidade de cautela e firmeza da politica. "
            + "Quando o original trouxer sinais favoraveis, preserve-os, mas qualifique-os como ainda insuficientes, condicionados ou sujeitos a confirmacao. "
            + "Quando o original trouxer riscos, preserve a informacao e torne o enquadramento mais firme, sem criar fundamento ou decisao nova."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE COM EXPANSAO CONTROLADA:\n"
            + "Reescreva para um tom institucional mais paciente, gradualista e confiante. "
            + "Diante dos mesmos fatos, aumente a saliencia de defasagens da politica, sinais de acomodacao, progresso de desinflacao, convergencia, ociosidade, custos de aperto excessivo sobre atividade e emprego, e leitura mais balanceada dos riscos. "
            + "Quando termos de risco, pressao ou inflacao forem factuais e precisarem permanecer, preserve-os, mas enquadre sua urgencia com monitoramento, gradualismo, simetria do balanco ou sinais favoraveis ja presentes no proprio texto. "
            + "Reduza severidade sem negar fatos, trocar sinais, criar fundamento ou decisao nova."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_register_rewrite_prompt(stance: str) -> str:
    """Bounded expansion plus Copom-register families learned from old texts."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve ser forte o suficiente para separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para explicitar o mesmo enquadramento institucional; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

FAMILIAS DE REGISTRO A REBALANCEAR
- Use familias convencionais do Copom, conforme couber ao proprio texto: riscos inflacionarios, incerteza, persistencia, pressao inflacionaria, ancoragem de expectativas, vigilancia, cautela, restricao monetaria, convergencia, desinflacao, ociosidade, defasagens da politica, gradualismo, atividade e emprego.
- Nao trate essas familias como lista obrigatoria; escolha apenas as que forem compativeis com os fatos ja presentes em cada paragrafo.
- Evite repetir mecanicamente as mesmas expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK NO REGISTRO COPOM:\n"
            + "Reescreva para aumentar a severidade institucional diante dos mesmos fatos. "
            + "Destaque riscos inflacionarios, persistencia de pressoes, incerteza, custo de desancoragem, necessidade de vigilancia, cautela e firmeza da restricao monetaria quando esses temas forem compativeis com o paragrafo. "
            + "Sinais de melhora devem ser preservados, mas apresentados como condicionados, incipientes ou insuficientes para reduzir a preocupacao. "
            + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE NO REGISTRO COPOM:\n"
            + "Reescreva para aumentar paciencia, gradualismo e conforto institucional diante dos mesmos fatos. "
            + "Destaque defasagens da politica, ociosidade, acomodacao, progresso de desinflacao, convergencia, custos de aperto excessivo, atividade e emprego quando esses temas forem compativeis com o paragrafo. "
            + "Se termos de risco ou pressao precisarem permanecer por serem factuais, preserve-os, mas repondere sua urgencia com qualificadores de monitoramento, simetria, cautela na calibragem e sinais favoraveis ja contidos no texto. "
            + "Nao negue riscos, nao troque sinais e nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_dove_rebalance_rewrite_prompt(stance: str) -> str:
    """Asymmetric register prompt that targets the weak dove arm.

    This version is based on the 2026-06-07 old-artifact audit. The hawk arm is
    intentionally kept at the register-v1 design; the dove arm gets a stronger
    but still factual rebalancing rule.
    """
    if stance == "hawk":
        return build_calibrated_register_rewrite_prompt(stance)
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance != "dove":
        raise ValueError(f"Unknown stance {stance!r}")

    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve tornar o texto nitidamente mais paciente e menos restritivo, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

REBALANCEAMENTO DOVISH DEFENSAVEL
- Realoque o peso avaliativo para paciencia, gradualismo e conforto institucional quando isso for compativel com o proprio texto.
- Se o texto mencionar atividade fraca, mercado de trabalho, utilizacao de fatores, demanda, hiato ou capacidade ociosa, use esses elementos como amortecedores de pressoes de precos.
- Se o texto mencionar inflacao, expectativas, projecoes ou convergencia, enfatize sinais de processo, horizonte relevante, monitoramento e condicionalidade em vez de urgencia restritiva.
- Se o texto mencionar choques, riscos ou pressoes, preserve esses fatos, mas nao amplifique sua severidade. Enquadre-os com transitoriedade, defasagens da politica, balanco mais simetrico, cautela na calibragem ou sinais favoraveis ja presentes.
- Evite adicionar nova linguagem de vigilancia, perseveranca, desancoragem, restricao monetaria, aperto ou assimetria altista, a menos que essa linguagem ja esteja no texto original; nesse caso, mantenha-a sem reforcar e contrabalanceie com paciencia e gradualismo.
- Nao trate familias de registro como lista obrigatoria nem repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE REBALANCEADO NO REGISTRO COPOM:\n"
        + "Reescreva para tornar o texto claramente mais paciente, gradualista e menos alarmado do que o original. "
        + "Use o proprio material factual do paragrafo para destacar ociosidade, desinflacao, convergencia, defasagens, atividade, emprego, custos de aperto excessivo e cautela na calibragem. "
        + "Quando riscos ou pressoes forem parte factual do texto, preserve-os, mas reduza sua urgencia retorica por meio de qualificadores de monitoramento, gradualismo, horizonte relevante e sinais favoraveis ja contidos no trecho. "
        + "O resultado deve soar como uma ata oficial mais branda, sem negar riscos, trocar sinais, criar fundamentos ou alterar a decisao."
    )


def build_calibrated_dove_rebalance_v2_rewrite_prompt(stance: str) -> str:
    """Dove rebalance with a stricter guard against added restrictive register."""
    if stance == "hawk":
        return build_calibrated_register_rewrite_prompt(stance)
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance != "dove":
        raise ValueError(f"Unknown stance {stance!r}")

    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve tornar o texto nitidamente mais paciente, gradualista e menos restritivo, sem alterar fatos, decisoes, numeros, direcao causal, condicionalidades ou sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

REBALANCEAMENTO DOVISH COM GUARDA DE REGISTRO
- Use o proprio material factual do paragrafo para deslocar a avaliacao para paciencia, gradualismo, horizonte relevante, defasagens da politica e custos de aperto excessivo.
- Quando houver atividade fraca, desemprego, utilizacao de fatores, hiato, demanda ou recuperacao gradual, explicite que esses elementos amortecem repasses de precos e favorecem convergencia ao longo do horizonte relevante.
- Quando houver inflacao, expectativas ou projecoes, preserve os fatos e enfatize processo, horizonte, monitoramento, convergencia e efeitos defasados da politica ja implementada.
- Quando houver choques, riscos ou pressao inflacionaria, preserve-os, mas nao acrescente novas expressoes de alerta. Enquadre-os como elementos a monitorar, condicionados, parcialmente mitigados ou balanceados por sinais favoraveis presentes no proprio texto.
- Evite acrescentar vocabulario restritivo quando ele nao estiver no original, especialmente: cautela, vigilante, vigilancia, perseveranca, desancoragem, persistencia, restricao, restritivo, contracionista, aperto, assimetria altista e pressoes.
- Se alguma dessas expressoes ja estiver no texto original e precisar permanecer, mantenha-a sem repetir nem intensificar, e contrabalanceie com gradualismo, defasagens, atividade, emprego, ociosidade, desinflacao ou convergencia quando esses elementos tambem estiverem no texto.
- Nao use uma lista fixa de palavras e nao repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE REBALANCEADO V2:\n"
        + "Reescreva como uma ata oficial mais branda e paciente. "
        + "Priorize leitura de processo, acomodacao, gradualismo, horizonte relevante, defasagens, atividade e emprego. "
        + "Troque reforcos de urgencia por formulacoes de acompanhamento e calibragem gradual; preserve riscos factuais, mas sem torna-los mais salientes que no original. "
        + "O texto final deve ser menos alarmado que o original e que uma versao restritiva, sem negar riscos, trocar sinais, criar fundamentos ou alterar a decisao."
    )


def build_calibrated_symmetric_rebalance_rewrite_prompt(stance: str) -> str:
    """Symmetric high-dose register prompt.

    Both arms receive mirrored instructions: preserve factual opposing evidence
    but do not amplify the opposite arm's register unless it is already in the
    source text and balanced by same-arm evaluative framing.
    """
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual simetrico de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal, mesmas condicionalidades e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

REBALANCEAMENTO SIMETRICO DEFENSAVEL
- Aplique a mesma intensidade de tratamento nos dois bracos: cada instrucao de saliencia em um braco tem uma contrapartida simetrica no outro.
- Nao trate familias de registro como lista obrigatoria nem repita mecanicamente expressoes. Escolha apenas familias compativeis com os fatos ja presentes no paragrafo.
- A mudanca deve aparecer como reponderacao institucional de risco, paciencia, urgencia, horizonte, persistencia/transitoriedade, balanco de custos e grau de conforto, nao como insercao artificial de dicionario.
- Guarda contra registro do braco oposto: preserve evidencias factuais contrarias quando elas existirem, mas nao as amplifique retoricamente nem acrescente vocabulario do braco oposto que nao esteja sustentado pelo texto original.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK SIMETRICO:\n"
            + "Reescreva para um tom institucional mais rigoroso, vigilante e menos complacente diante dos mesmos fatos. "
            + "Quando o texto trouxer inflacao, expectativas, riscos, choques ou projecoes, repondere a avaliacao para maior saliencia de persistencia, incerteza, riscos inflacionarios, ancoragem de expectativas, necessidade de vigilancia, cautela na conducao e firmeza da politica quando esses temas forem compativeis com o paragrafo. "
            + "Quando o texto trouxer atividade fraca, ociosidade, desinflacao, convergencia, gradualismo ou defasagens, preserve esses fatos, mas nao amplifique conforto, paciencia ou acomodacao alem do original; apresente-os como condicionados, incipientes, insuficientes ou sujeitos a confirmacao quando isso for compativel com o proprio texto. "
            + "Evite acrescentar vocabulario de conforto, acomodacao, desinflacao, convergencia facil, ociosidade como garantia ou gradualismo paciente quando ele nao estiver no original; se estiver, mantenha-o sem repetir nem intensificar e contrabalanceie com riscos, incerteza, persistencia ou ancoragem tambem presentes no texto. "
            + "O resultado deve soar como uma ata oficial mais firme, sem negar sinais favoraveis, trocar sinais, criar fundamentos ou alterar a decisao."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE SIMETRICO:\n"
            + "Reescreva para um tom institucional mais paciente, gradualista e menos restritivo diante dos mesmos fatos. "
            + "Quando o texto trouxer atividade fraca, ociosidade, mercado de trabalho, utilizacao de fatores, desinflacao, convergencia, gradualismo ou defasagens, repondere a avaliacao para maior saliencia de horizonte relevante, acomodacao, custos de aperto excessivo, atividade, emprego e calibragem gradual quando esses temas forem compativeis com o paragrafo. "
            + "Quando o texto trouxer inflacao, expectativas, riscos, choques, pressoes ou projecoes, preserve esses fatos, mas nao amplifique alerta, persistencia, desancoragem, vigilancia, restricao ou aperto alem do original; apresente-os como monitorados, condicionados, parcialmente mitigados ou balanceados por sinais favoraveis quando isso for compativel com o proprio texto. "
            + "Evite acrescentar vocabulario restritivo, de vigilancia, perseveranca, desancoragem, persistencia, restricao, contracionismo, aperto, assimetria altista ou pressoes quando ele nao estiver no original; se estiver, mantenha-o sem repetir nem intensificar e contrabalanceie com gradualismo, defasagens, atividade, emprego, ociosidade, desinflacao ou convergencia tambem presentes no texto. "
            + "O resultado deve soar como uma ata oficial mais branda, sem negar riscos, trocar sinais, criar fundamentos ou alterar a decisao."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_symmetric_rebalance_v2_rewrite_prompt(stance: str) -> str:
    """Symmetric high-dose prompt with matched positive obligations."""
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual simetrico de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal, mesmas condicionalidades e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

TRATAMENTO SIMETRICO DE DOSE
- Aplique a mesma intensidade de tratamento nos dois bracos. Cada obrigacao positiva em um braco tem uma contrapartida de mesma forca no outro.
- Em cada paragrafo com material avaliativo e suporte factual suficiente, faca tres tipos de mudanca: saliencia do fator relevante, qualificadores/modais, e enquadramento de horizonte ou custo.
- Preserve evidencias factuais contrarias quando elas existirem, mas nao as amplifique retoricamente nem acrescente vocabulario do braco oposto que nao esteja sustentado pelo texto original.
- Nao trate familias de registro como lista obrigatoria nem repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    if stance == "hawk":
        return (
            base
            + "\nOBRIGACOES POSITIVAS - HAWK SIMETRICO V2:\n"
            + "1) Saliencia: quando o paragrafo trouxer inflacao, expectativas, riscos, choques, projecoes ou incerteza, torne esses fatores mais centrais na avaliacao institucional, sem criar fatos novos. "
            + "2) Qualificadores e modais: aumente a forca de cautela, vigilancia, persistencia, ancoragem de expectativas, assimetria de riscos ou firmeza da politica quando esses temas forem compativeis com o texto. "
            + "3) Horizonte e custo: enquadre o custo de agir tarde, de tolerar desvios da meta ou de permitir deterioracao das expectativas, sempre a partir de elementos ja presentes. "
            + "4) Evidencia contraria: quando o texto trouxer atividade fraca, ociosidade, desinflacao, convergencia, gradualismo ou defasagens, preserve esses fatos, mas apresente-os como condicionados, incipientes, insuficientes ou sujeitos a confirmacao quando isso for compativel com o proprio texto. "
            + "5) Guarda simetrica: evite acrescentar vocabulario de conforto, acomodacao, convergencia facil, ociosidade como garantia ou gradualismo paciente quando ele nao estiver no original; se estiver, mantenha-o sem repetir nem intensificar e contrabalanceie com riscos, incerteza, persistencia ou ancoragem tambem presentes no texto. "
            + "O resultado deve soar como uma ata oficial mais firme, sem negar sinais favoraveis, trocar sinais, criar fundamentos ou alterar a decisao."
        )
    if stance == "dove":
        return (
            base
            + "\nOBRIGACOES POSITIVAS - DOVE SIMETRICO V2:\n"
            + "1) Saliencia: quando o paragrafo trouxer atividade, emprego, utilizacao de fatores, hiato, ociosidade, desinflacao, convergencia, gradualismo ou defasagens, torne esses fatores mais centrais na avaliacao institucional, sem criar fatos novos. "
            + "2) Qualificadores e modais: aumente a forca de paciencia, calibragem gradual, horizonte relevante, efeitos defasados, acomodacao de choques ou custos de aperto excessivo quando esses temas forem compativeis com o texto. "
            + "3) Horizonte e custo: enquadre o custo de agir em excesso, de apertar alem do necessario ou de desconsiderar defasagens sobre atividade e emprego, sempre a partir de elementos ja presentes. "
            + "4) Evidencia contraria: quando o texto trouxer inflacao, expectativas, riscos, choques, pressoes, projecoes ou incerteza, preserve esses fatos, mas apresente-os como monitorados, condicionados, parcialmente mitigados ou balanceados por sinais favoraveis quando isso for compativel com o proprio texto. "
            + "5) Guarda simetrica: evite acrescentar vocabulario restritivo, de vigilancia, perseveranca, desancoragem, persistencia, restricao, contracionismo, aperto, assimetria altista ou pressoes quando ele nao estiver no original; se estiver, mantenha-o sem repetir nem intensificar e contrabalanceie com gradualismo, defasagens, atividade, emprego, ociosidade, desinflacao ou convergencia tambem presentes no texto. "
            + "O resultado deve soar como uma ata oficial mais branda, sem negar riscos, trocar sinais, criar fundamentos ou alterar a decisao."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")


def build_calibrated_symmetric_rebalance_v3_rewrite_prompt(stance: str) -> str:
    """Mirror dove-rebalance-v2 by preserving its dove arm and adding a hawk counterpart."""
    if stance == "dove":
        return build_calibrated_dove_rebalance_v2_rewrite_prompt(stance)
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance != "hawk":
        raise ValueError(f"Unknown stance {stance!r}")

    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve tornar o texto nitidamente mais severo, vigilante e menos complacente, sem alterar fatos, decisoes, numeros, direcao causal, condicionalidades ou sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

REBALANCEAMENTO HAWKISH COM GUARDA DE REGISTRO
- Use o proprio material factual do paragrafo para deslocar a avaliacao para cautela, vigilancia, persistencia inflacionaria, ancoragem das expectativas, incerteza e custos de agir tarde.
- Quando houver inflacao, expectativas, riscos, choques, pressoes ou projecoes, explicite que esses elementos exigem monitoramento, firmeza e avaliacao prudente do horizonte relevante.
- Quando houver atividade fraca, desemprego, utilizacao de fatores, hiato, demanda ou recuperacao gradual, preserve os fatos, mas enfatize que eles nao eliminam riscos, podem ser insuficientes para assegurar convergencia ou dependem de confirmacao adicional.
- Quando houver desinflacao, convergencia, gradualismo ou defasagens da politica, preserve-os, mas nao acrescente novas expressoes de conforto. Enquadre-os como elementos condicionais, incipientes, parciais ou sujeitos a confirmacao diante dos riscos presentes no proprio texto.
- Evite acrescentar vocabulario de acomodacao quando ele nao estiver no original, especialmente: paciencia, gradualismo paciente, acomodacao, conforto, convergencia facil, afrouxamento, custos de aperto excessivo, atividade como garantia e ociosidade como garantia.
- Se alguma dessas expressoes ja estiver no texto original e precisar permanecer, mantenha-a sem repetir nem intensificar, e contrabalanceie com cautela, vigilancia, incerteza, persistencia, ancoragem, riscos ou pressoes quando esses elementos tambem estiverem no texto.
- Nao use uma lista fixa de palavras e nao repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    return (
        base
        + "\nINSTRUCOES DE ESTILO - HAWK REBALANCEADO V3:\n"
        + "Reescreva como uma ata oficial mais firme e vigilante. "
        + "Priorize leitura de risco, cautela, persistencia, ancoragem, horizonte relevante, incerteza, monitoramento e firmeza. "
        + "Troque reforcos de conforto por formulacoes de prudencia e necessidade de confirmacao; preserve sinais favoraveis factuais, mas sem torna-los mais salientes que no original. "
        + "O texto final deve ser menos acomodativo que o original e que uma versao branda, sem negar sinais favoraveis, trocar sinais, criar fundamentos ou alterar a decisao."
    )


def build_calibrated_symmetric_rebalance_v4_rewrite_prompt(stance: str) -> str:
    """Near-register hawk with the successful dove-v2 arm preserved."""
    if stance == "dove":
        return build_calibrated_dove_rebalance_v2_rewrite_prompt(stance)
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance != "hawk":
        raise ValueError(f"Unknown stance {stance!r}")

    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve ser forte o suficiente para separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para reponderar o mesmo conteudo; nao acrescente fatos.
   - O texto pode ficar moderadamente maior quando houver material avaliativo, mas deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

TRATAMENTO DE REGISTRO
- Use o proprio material factual de cada paragrafo para reponderar o tom; nao crie dados, causas ou diagnosticos.
- Evite repetir mecanicamente as mesmas expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    return (
        base
        + "\nINSTRUCOES DE ESTILO - HAWK NO REGISTRO COPOM V4:\n"
        + "Reescreva para aumentar a severidade institucional diante dos mesmos fatos. "
        + "Destaque riscos inflacionarios, persistencia de pressoes, incerteza, custo de desancoragem, necessidade de vigilancia, cautela e firmeza da restricao monetaria quando esses temas forem compativeis com o paragrafo. "
        + "Sinais de melhora devem ser preservados, mas apresentados como condicionados, incipientes ou insuficientes para reduzir a preocupacao. "
        + "Nao acrescente vocabulario de conforto, acomodacao, convergencia facil, ociosidade como garantia ou gradualismo paciente quando ele nao estiver sustentado pelo texto original. "
        + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
    )


def build_calibrated_symmetric_rebalance_v5_rewrite_prompt(stance: str) -> str:
    """Symmetric block-level intensity prompt with matched positive treatment floors."""
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance not in {"hawk", "dove"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal, mesmas condicionalidades e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nao alongue trechos puramente factuais.
   - Nos paragrafos avaliativos, use expansao retorica moderada para tornar a reponderacao visivel; uma frase curta adicional e aceitavel quando houver suporte factual suficiente.
   - O texto deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

TRATAMENTO SIMETRICO DE INTENSIDADE
- Aplique a mesma intensidade de tratamento nos dois bracos. Nao trate um braco apenas como remocao do outro.
- Em cada paragrafo avaliativo com suporte factual suficiente, faca duas reponderacoes institucionais: uma de saliencia do fator relevante e outra de qualificadores, horizonte, custo ou condicionalidade.
- Quando o paragrafo tiver fatos favoraveis e desfavoraveis, preserve ambos, mas faca a hierarquia avaliativa favorecer claramente o braco solicitado.
- Nao use uma lista fixa de palavras e nao repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK COM INTENSIDADE V5:\n"
            + "Reescreva para aumentar a severidade institucional diante dos mesmos fatos. "
            + "Destaque riscos inflacionarios, persistencia de pressoes, incerteza, custo de desancoragem, necessidade de vigilancia, cautela e firmeza da restricao monetaria quando esses temas forem compativeis com o paragrafo. "
            + "Sinais de melhora devem ser preservados, mas apresentados como condicionados, incipientes ou insuficientes para reduzir a preocupacao. "
            + "Quando houver atividade fraca, ociosidade, convergencia, desinflacao, gradualismo ou defasagens, preserve esses elementos, mas subordine-os a riscos, persistencia, incerteza ou necessidade de confirmacao quando esses riscos estiverem presentes. "
            + "Nao substitua o registro de severidade por prudencia generica, monitoramento generico ou mera confirmacao. "
            + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE COM INTENSIDADE V5:\n"
            + "Reescreva para aumentar a paciencia institucional diante dos mesmos fatos. "
            + "Destaque processo de desinflacao, convergencia, atividade, emprego, ociosidade, horizonte relevante, defasagens da politica e custos de aperto excessivo quando esses temas forem compativeis com o paragrafo. "
            + "Riscos, choques e pressoes devem ser preservados, mas apresentados como monitorados, condicionados, parcialmente mitigados ou balanceados por sinais favoraveis quando houver suporte no proprio texto. "
            + "Quando houver inflacao, expectativas, riscos, choques, pressoes ou projecoes, preserve esses elementos, mas subordine-os a horizonte, gradualismo, defasagens, atividade, emprego, ociosidade, desinflacao ou convergencia quando esses fatores estiverem presentes. "
            + "Nao substitua o registro de paciencia por otimismo generico, conforto generico ou simples suavizacao. "
            + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    raise ValueError(f"Unknown stance {stance!r}")


def _build_symmetric_sweep_base(title: str, treatment: str) -> str:
    return f"""Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata como um contrafactual de estilo comunicacional plausivel no registro historico do Copom. A intervencao deve separar claramente os bracos hawk e dove, mas deve permanecer defensavel: mesmos fatos, mesmas decisoes, mesmos numeros, mesma direcao causal, mesmas condicionalidades e mesma sequencia.

REGRAS RIGIDAS
1) PRESERVACAO FACTUAL:
   - Nao adicione fatos, eventos, causas, diagnosticos, previsoes, instrumentos, decisoes ou implicacoes de politica que nao estejam no texto original.
   - Nao remova fatos relevantes do texto original.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, casas decimais ou referencia temporal.
   - Preserve entidades, relacoes causais, direcao das afirmacoes, condicionalidades, titulos/cabecalhos e ordem dos topicos.

2) ESTRUTURA E TAMANHO:
   - Preserve o numero de paragrafos nao vazios.
   - Preserve literalmente tabelas, linhas de tabela, listas, nomes de votantes, decisoes, taxas, datas e trechos puramente factuais.
   - Nao alongue trechos puramente factuais.
   - Em paragrafos avaliativos, pode haver expansao retorica moderada quando ela apenas reponderar fatos ja presentes.
   - O texto deve continuar parecendo uma ata oficial, concisa e tecnica.

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito.
   - Nao inclua comentarios, explicacoes, rotulos ou notas metodologicas.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione metalinguagem sobre a tarefa, rotulos de tom, "prompt", "versao", "reescrito" ou "abaixo esta".

TRATAMENTO SIMETRICO - {title}
{treatment}
- Nao use uma lista fixa de palavras e nao repita mecanicamente expressoes. A mudanca deve aparecer como reponderacao institucional, nao como insercao artificial de dicionario.
"""


def build_calibrated_symmetric_rebalance_v6_rewrite_prompt(stance: str) -> str:
    """Concise dual-register prompt: compact hawk and compact dove."""
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance not in {"hawk", "dove"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = _build_symmetric_sweep_base(
        "REGISTRO CONCISO V6",
        "- Em cada paragrafo avaliativo com suporte factual suficiente, inclua uma reponderacao clara do braco solicitado.\n"
        "- A reponderacao deve ser curta, em linguagem de ata, e deve preservar evidencias contrarias sem deixa-las comandar a conclusao avaliativa.",
    )
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK REGISTRO CONCISO V6:\n"
            + "Reescreva para aumentar a severidade institucional diante dos mesmos fatos. "
            + "Destaque riscos inflacionarios, persistencia de pressoes, incerteza, custo de desancoragem, necessidade de vigilancia, cautela e firmeza da restricao monetaria quando esses temas forem compativeis com o paragrafo. "
            + "Sinais de melhora devem ser preservados, mas apresentados como condicionados, incipientes ou insuficientes para reduzir a preocupacao. "
            + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE REGISTRO CONCISO V6:\n"
        + "Reescreva para aumentar a paciencia institucional diante dos mesmos fatos. "
        + "Destaque processo de desinflacao, convergencia, ociosidade, hiato, atividade, emprego, horizonte relevante, defasagens da politica e calibragem gradual quando esses temas forem compativeis com o paragrafo. "
        + "Riscos e pressoes devem ser preservados, mas apresentados como monitorados, condicionados, parcialmente mitigados ou insuficientes para justificar maior urgencia quando houver sinais favoraveis no proprio texto. "
        + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
    )


def build_calibrated_symmetric_rebalance_v7_rewrite_prompt(stance: str) -> str:
    """Symmetric bounded-length prompt inspired by the original artifact length."""
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance not in {"hawk", "dove"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = _build_symmetric_sweep_base(
        "EXPANSAO MODERADA V7",
        "- Em paragrafos avaliativos, desenvolva a avaliacao de forma um pouco mais completa que no original, mantendo concisao de ata.\n"
        "- Quando houver material avaliativo suficiente, acrescente uma frase curta ou uma subordinada que explicite a leitura de horizonte, custo ou risco do braco solicitado.\n"
        "- A expansao deve ser comparavel nos dois bracos; nao alongue um braco e comprima o outro por desenho.",
    )
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK EXPANSAO MODERADA V7:\n"
            + "Reforce a leitura de riscos inflacionarios, persistencia de pressoes, incerteza, desancoragem, vigilancia, cautela e restricao monetaria. "
            + "Quando houver sinais benignos, preserve-os, mas acrescente a avaliacao de que permanecem condicionados, incompletos ou insuficientes diante do balanco de riscos. "
            + "Use a expansao apenas para tornar explicita a severidade institucional ja compatvel com os fatos."
        )
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE EXPANSAO MODERADA V7:\n"
        + "Reforce a leitura de desinflacao, convergencia, ociosidade, hiato, atividade, emprego, defasagens, horizonte relevante e calibragem gradual. "
        + "Quando houver riscos ou pressoes, preserve-os, mas acrescente a avaliacao de que sao monitorados, condicionados, parcialmente mitigados ou balanceados pelos sinais favoraveis presentes. "
        + "Use a expansao apenas para tornar explicita a paciencia institucional ja compativel com os fatos."
    )


def build_calibrated_symmetric_rebalance_v8_rewrite_prompt(stance: str) -> str:
    """Symmetric paragraph intervention prompt adapted from the legacy style."""
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance not in {"hawk", "dove"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = _build_symmetric_sweep_base(
        "INTERVENCOES POR PARAGRAFO V8",
        "- Em cada paragrafo avaliativo, faca pelo menos tres intervencoes de registro quando houver suporte factual: saliencia, modal/qualificador e conclusao avaliativa.\n"
        "- Em paragrafos quase puramente factuais, preserve o texto com mudancas minimas.\n"
        "- As intervencoes devem ser distribuidas pelo paragrafo, nao concentradas apenas na ultima frase.",
    )
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK INTERVENCOES V8:\n"
            + "Nas intervencoes, prefira severidade, cautela, vigilancia, persistencia, incerteza, pressoes inflacionarias, desancoragem, balanco de riscos e firmeza da politica monetaria. "
            + "Elementos de atividade, ociosidade ou convergencia devem permanecer, mas como condicionados ou insuficientes para eliminar preocupacao quando houver riscos no proprio texto. "
            + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
        )
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE INTERVENCOES V8:\n"
        + "Nas intervencoes, prefira paciencia, gradualismo, calibragem, horizonte relevante, defasagens, desinflacao, convergencia, ociosidade, atividade e emprego. "
        + "Elementos de risco, inflacao ou pressoes devem permanecer, mas como monitorados, condicionados ou balanceados por sinais favoraveis quando houver suporte no proprio texto. "
        + "Nao crie recomendacao, decisao, choque, dado ou diagnostico novo."
    )


def build_calibrated_symmetric_rebalance_v9_rewrite_prompt(stance: str) -> str:
    """Symmetric hierarchy prompt for mixed-evidence paragraphs."""
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    if stance not in {"hawk", "dove"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = _build_symmetric_sweep_base(
        "HIERARQUIA AVALIATIVA V9",
        "- Quando o paragrafo contiver sinais em direcoes diferentes, preserve todos os sinais, mas organize a hierarquia avaliativa para que o braco solicitado comande a leitura final.\n"
        "- Em paragrafos mistos, a frase final deve consolidar a leitura do braco solicitado sem mudar a decisao ou os fatos.\n"
        "- Em paragrafos de uma unica direcao, intensifique a leitura ja presente sem criar fundamentos.",
    )
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK HIERARQUIA V9:\n"
            + "Faca com que riscos inflacionarios, persistencia de pressoes, incerteza, desancoragem, vigilancia, cautela e restricao monetaria comandem a conclusao avaliativa. "
            + "Sinais favoraveis devem aparecer antes da conclusao e ser qualificados como condicionados, incipientes ou insuficientes quando isso for compativel com o texto. "
            + "A leitura final deve soar mais severa, nao apenas mais prudente."
        )
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE HIERARQUIA V9:\n"
        + "Faca com que desinflacao, convergencia, ociosidade, hiato, atividade, emprego, defasagens, horizonte relevante e calibragem gradual comandem a conclusao avaliativa. "
        + "Riscos e pressoes devem aparecer antes da conclusao e ser qualificados como monitorados, condicionados ou balanceados quando isso for compativel com o texto. "
        + "A leitura final deve soar mais paciente, nao apenas menos severa."
    )


def build_calibrated_symmetric_rebalance_v10_rewrite_prompt(stance: str) -> str:
    """Symmetric final-clause dose prompt with compact Copom register."""
    if stance not in {"neutral", "hawk", "dove", "hawk_low", "hawk_high", "dove_low", "dove_high"}:
        raise ValueError(f"Unknown stance {stance!r}")

    base = _build_symmetric_sweep_base(
        "CLAUSULA AVALIATIVA FINAL V10",
        "- Em cada paragrafo avaliativo com suporte factual suficiente, preserve a substancia e acrescente ou ajuste uma clausula avaliativa final curta.\n"
        "- Essa clausula deve explicitar a implicacao institucional do paragrafo para o braco solicitado.\n"
        "- A clausula final deve ser natural no registro Copom e nao deve introduzir nova decisao, nova recomendacao ou novo dado.",
    )
    if stance == "neutral":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - NEUTRAL CLAUSULA FINAL V10:\n"
            + "A clausula avaliativa final deve preservar um tom neutro e tecnico diante dos mesmos fatos, sem deslocar a leitura para maior severidade hawk ou maior paciencia dove. "
            + "Quando houver avaliacao no paragrafo, formule a implicacao institucional de modo balanceado, mantendo riscos, sinais favoraveis, condicionalidades e incertezas com a mesma hierarquia substantiva do original. "
            + "O conjunto deve soar como ata oficial neutra diante dos mesmos fatos."
        )
    if stance == "hawk_low":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK BAIXA DOSE V10:\n"
            + "A clausula avaliativa final deve deslocar o texto apenas de forma moderada na direcao de maior cautela, vigilancia ou preocupacao com riscos inflacionarios, conforme o material do paragrafo. "
            + "Use uma intervencao breve e plausivel: preserve sinais favoraveis sem transforma-los em conforto pleno, e qualifique riscos sem tornar o paragrafo dramatico. "
            + "O conjunto deve soar levemente mais severo que o original, mas menos intenso que uma versao hawk de alta dose."
        )
    if stance in {"hawk", "hawk_high"}:
        return (
            base
            + "\nINSTRUCOES DE ESTILO - HAWK CLAUSULA FINAL V10:\n"
            + "A clausula avaliativa final deve reforcar cautela, vigilancia, persistencia de pressoes, incerteza, risco de desancoragem, assimetria altista ou firmeza da restricao monetaria, conforme o material do paragrafo. "
            + "Quando o paragrafo trouxer melhora, a clausula deve deixar claro que ela ainda nao elimina a preocupacao ou a necessidade de firmeza. "
            + "O conjunto deve soar como ata oficial mais severa diante dos mesmos fatos."
        )
    if stance == "dove_low":
        return (
            base
            + "\nINSTRUCOES DE ESTILO - DOVE BAIXA DOSE V10:\n"
            + "A clausula avaliativa final deve deslocar o texto apenas de forma moderada na direcao de maior paciencia, gradualismo ou confianca no horizonte relevante, conforme o material do paragrafo. "
            + "Use uma intervencao breve e plausivel: preserve riscos sem elimina-los e qualifique pressao inflacionaria sem negar os fatos descritos. "
            + "O conjunto deve soar levemente mais paciente que o original, mas menos intenso que uma versao dove de alta dose."
        )
    return (
        base
        + "\nINSTRUCOES DE ESTILO - DOVE CLAUSULA FINAL V10:\n"
        + "A clausula avaliativa final deve reforcar paciencia, gradualismo, horizonte relevante, defasagens, convergencia, desinflacao, ociosidade, atividade ou emprego, conforme o material do paragrafo. "
        + "Quando o paragrafo trouxer riscos, a clausula deve deixar claro que eles seguem monitorados e condicionados ao horizonte e aos sinais favoraveis presentes. "
        + "O conjunto deve soar como ata oficial mais paciente diante dos mesmos fatos."
    )


def build_legacy_series_rewrite_prompt(stance: str) -> str:
    """Prompt recovered from the legacy series-of-minutes generator.

    The text is kept in ASCII to avoid the encoding drift present in the
    archived script while preserving the substantive instructions.
    """
    base = """Voce e redator do Comite de Politica Monetaria (Copom).
Sua tarefa e reescrever o trecho fornecido da Ata preservando integralmente o conteudo factual, ou seja, criar um texto contrafactual que difere apenas na linguagem empregada, nao nos fatos em si.

REGRAS RIGIDAS (NAO NEGOCIAVEIS)
1) CONTEUDO FACTUAL:
   - Nao adicione novos fatos.
   - Nao altere, nao crie, nao adicione nem remova nenhum numero, data, percentual, sinal (+/-), unidade, moeda, ou casas decimais.
   - Nao complete anos (ex.: 96->1996). Copie numeros exatamente como aparecem.
   - O conteudo tido como factual deve ser integralmente preservado.

2) ESTRUTURA:
   - Preserve numero de paragrafos e ordem dos topicos.
   - Mantenha comprimento muito proximo ao original (faca substituicoes; evite acrescentar frases novas).

3) FORMATO:
   - Entregue SOMENTE o texto final reescrito, sem rotulos, comentarios ou explicacoes.
   - Nao crie markdown novo; preserve exatamente qualquer tabela, barra vertical, lista, bullet ou marcacao que ja exista no original.
   - Nao mencione "prompt", "hawkish", "dovish", "versao", "reescrito", "abaixo esta".

4) OBJETIVO DE CONTRASTE (IMPORTANTE):
   - Mesmo preservando fatos e estrutura, maximize a diferenca de TOM em relacao ao texto original.
   - Em CADA paragrafo, faca no minimo 5 intervencoes de estilo (substituicoes lexicais/modais), mantendo o sentido factual.
"""
    if stance == "hawk":
        return (
            base
            + "\nINSTRUCOES DE ESTILO (HAWKISH) - ORIENTACOES: "
            + "Trate choques, incertezas e riscos para a inflacao com muito mais preocupacao do que no texto original, "
            + "o tom mais firme, mais cauteloso, maior aversao a riscos inflacionarios e a desvios persistentes da meta. "
            + "Priorize escolhas lexicais que enfatizem vigilancia, persistencia e assimetria de riscos para cima. "
            + "De mais peso aos riscos de inflacao acima da meta e aos custos de expectativas desancoradas."
        )
    if stance == "dove":
        return (
            base
            + "\nINSTRUCOES DE ESTILO (DOVISH) - ORIENTACOES: "
            + "Seja MUITO mais brando e tranquilizador que o texto original, o tom deve ser mais confiante e menos alarmista; "
            + "maior enfase em atividade/emprego, defasagens e progresso da desinflacao. "
            + "Nao introduza vocabulario de teor restritivo/contracionista, de alerta maximo ou de risco inflacionario altista "
            + "(incluindo expressoes que sugiram endurecimento, vigilancia, perseveranca ou desancoragem), a nao ser que esse vocabulario "
            + "ja esteja presente no texto original; nesse caso, mantenha-o sem reforcar e sem amplificar."
        )
    if stance == "neutral":
        return BASE_REWRITE_PROMPT + "\nInstrucao de estilo:\n" + STANCE_INSTRUCTIONS["neutral"]
    raise ValueError(f"Unknown stance {stance!r}")
