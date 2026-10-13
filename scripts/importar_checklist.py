"""Gera compliance/config/criteria.yaml a partir da planilha do checklist.

    python scripts/importar_checklist.py                      # usa compliance/config/checklistPortalTransp.xlsx
    python scripts/importar_checklist.py --xlsx outro.xlsx --saida /tmp/criteria.yaml

O YAML gerado é a fonte de verdade da Camada 2 (versionado no git): cada quesito
ganha verificador, parâmetros, peso, dimensão, aplicabilidade e onda. Ajustes finos
(termos, sinônimos, pesos) devem ser feitos no YAML; este script serve para
recriá-lo quando a planilha mudar. Mudar o YAML gera nova `checklist_version`
(hash), mantendo o histórico comparável.

Pesos (heurística documentada — conferir com a Cartilha PNTP):
  3 = essencial: fundamentação na LRF (LC 101) ou quesitos de existência centrais
  2 = obrigatório: demais (inclui meta-critérios de pesquisa, exportação, atualidade, histórico)
  1 = recomendado: blocos de boas práticas (17, 18 e 21)
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import openpyxl
import yaml

RAIZ = Path(__file__).resolve().parent.parent
CFG = RAIZ / "compliance" / "config"

# ---------------------------------------------------------------- vocabulário

R_ENDERECO = r"\b\d{5}-?\d{3}\b|\b(rua|avenida|av\.|praca|praça|estrada|rodovia|travessa|alameda)\b"
R_TELEFONE = r"\(?\b\d{2}\)?\s?\d{4,5}[-\s]?\d{4}\b|\b0800[\s-]?\d{3}[\s-]?\d{4}\b"
R_EMAIL = r"[\w.+-]+@[\w-]+\.[\w.]+"
R_HORARIO = r"\b\d{1,2}\s?h(\d{2})?\s?(as|às|a|-|até)\s?\d{1,2}\s?h|\bhor[aá]rio de (atendimento|funcionamento)\b"

SECAO_BLOCO = {
    "1": [], "2": ["institucional"], "3": ["receita"], "4": ["despesa"], "5": ["pessoal"],
    "6": ["diarias"], "7": ["licitacoes"], "8": ["contratos"], "9": ["patrimonio"], "10": ["patrimonio"],
    "11": ["obras"], "12": ["planejamento_contas"], "13": ["emendas"], "14": ["sic"], "15": ["sic"],
    "16": ["acessibilidade"], "17": ["ouvidoria"], "18": ["lgpd_digital"], "19": ["planejamento_contas"],
    "20": ["rreo"], "21": ["boas_praticas"],
}
DIMENSAO = {
    **{b: "institucional_acesso" for b in ("1", "2", "14", "15", "16", "17", "18", "21")},
    **{b: "receita_despesa" for b in ("3", "4", "13")},
    **{b: "pessoal" for b in ("5", "6")},
    **{b: "licitacoes_contratos_obras" for b in ("7", "8", "9", "10", "11")},
    **{b: "planejamento_contas" for b in ("12", "19", "20")},
}
ONDA = {}
for faixa, onda in (
    ("1.1-1.3 2.1-2.9 14.1-14.4 15.1 15.3 16.1-16.5 17.1-17.3 18.1 18.3 12.1 19.1 19.3 19.5 20.1", 1),
    ("3.1-3.7 4.1-4.7 5.1-5.11 7.1 7.7-7.10 8.1 8.4-8.7 12.2-12.4 20.2-20.4", 2),
    ("5.12 6.1-6.11 7.2-7.6 8.2 8.3 8.8 9.1-9.2 10.1-10.2 12.5 12.6 15.2 15.4-15.7 18.2 18.4 "
     "19.2 19.4 19.6 19.7 20.5-20.9", 3),
    ("3.8-3.12 4.8-4.13 11.1-11.4 13.2-13.8 21.1-21.4", 4),
):
    for parte in faixa.split():
        a, _, b = parte.partition("-")
        bloco, ini = a.split(".")
        fim = b.split(".")[1] if b else ini
        for i in range(int(ini), int(fim) + 1):
            ONDA[f"{bloco}.{i}"] = onda


def ex(termos, secoes=None, regex=None, escopo="secao"):
    p = {"termos": termos}
    if secoes is not None:
        p["secoes"] = secoes
    if regex:
        p["regex"] = regex
    if escopo != "secao":
        p["escopo"] = escopo
    return ("existence", p)


def sf(campos, minimo=None, secoes=None):
    p = {"campos": campos}
    if minimo:
        p["minimo_parcial"] = minimo
    if secoes:
        p["secoes"] = secoes
    return ("structured_fields", p)


def doc(*grupos, secoes=None):
    p = {"grupos": [list(g) for g in grupos]}
    if secoes:
        p["secoes"] = secoes
    return ("document", p)


def sem(consulta, termos, secoes=None):
    p = {"consulta": consulta, "termos": termos}
    if secoes:
        p["secoes"] = secoes
    return ("semantic", p)


def meta(tipo, secoes=None, filtro=None, referencia=None):
    p = {}
    if secoes:
        p["secoes"] = secoes
    if filtro:
        p["filtro"] = filtro
    if referencia:
        p["referencia"] = referencia
    return (tipo, p)


RGF = ["relatorio de gestao fiscal", "rgf"]
RREO = ["relatorio resumido da execucao orcamentaria", "rreo"]
IMOV = ["imoveis", "bens imoveis", "imovel"]
VEIC = ["veiculos", "frota", "veiculo"]

# ---------------------------------------------------------------- mapeamento quesito -> verificador

MAPA: dict[str, tuple[str, dict]] = {
    "1.1": ("portal", {}),
    "1.2": ("homepage_link", {}),
    "1.3": ("search_filter", {"escopo": "site"}),
    "2.1": ex(["competencias", "atribuicoes", "competencia"]),
    "2.2": ex(["estrutura organizacional", "organograma"]),
    "2.3": ex(["secretarios", "secretariado", "quem e quem", "gestores", "prefeito", "vice prefeito",
               "responsaveis", "dirigentes"]),
    "2.4": ex(["endereco", "enderecos", "localizacao", "contato"], regex=[R_ENDERECO], escopo="site"),
    "2.5": ex(["telefone", "telefones", "contato"], regex=[R_TELEFONE], escopo="site"),
    "2.6": ex(["horario de atendimento", "horario de funcionamento", "expediente"], regex=[R_HORARIO], escopo="site"),
    "2.7": ex(["perguntas frequentes", "faq", "perguntas e respostas", "duvidas frequentes"], escopo="site"),
    "2.8": sem("atos normativos próprios: leis municipais, decretos, portarias, resoluções e diário oficial",
               ["decreto", "decretos", "lei municipal", "leis municipais", "portaria", "portarias",
                "legislacao", "atos normativos", "diario oficial"]),
    "2.9": ex(["fale conosco", "contato", "contatos", "ouvidoria", "atendimento ao cidadao"], escopo="site"),
    "3.1": sf({"arrecadada": ["arrecadad", "realizad", "valor arrecadado", "receita realizada"]}),
    "3.2": sf({"prevista": ["previst", "orcad", "previsao", "valor previsto", "dotacao"]}),
    "3.3": sf({"categoria": ["categoria economica", "categoria"], "origem": ["origem"], "especie": ["especie"],
               "fonte": ["fonte de recurso", "fonte"]}, minimo=2),
    "3.4": meta("search_filter", ["receita"]),
    "3.5": meta("export", ["receita"]),
    "3.6": meta("freshness", ["receita"], referencia="mensal"),
    "3.7": meta("history", ["receita"]),
    "3.8": sf({"instrumento": ["convenio", "acordo", "instrumento", "transferencia"],
               "valor": ["valor", "total previsto", "valor previsto", "valor do repasse"]}, minimo=1,
              secoes=["transferencias", "receita"]),
    "3.9": meta("search_filter", ["transferencias"]),
    "3.10": meta("export", ["transferencias"]),
    "3.11": meta("freshness", ["transferencias"], referencia="mensal"),
    "3.12": meta("history", ["transferencias"]),
    "4.1": sf({"empenho": ["empenh"], "liquidacao": ["liquidac", "liquidad"], "pagamento": ["pago", "pagament"]},
              minimo=2),
    "4.2": sf({"unidade": ["unidade orcamentaria", "unidade gestora", "orgao", "unidade"], "funcao": ["funcao"],
               "subfuncao": ["subfuncao"], "programa": ["programa"], "acao": ["acao", "projeto atividade"],
               "natureza": ["natureza", "elemento"], "fonte": ["fonte"]}, minimo=4),
    "4.3": sf({"credor": ["credor", "favorecid", "beneficiari", "fornecedor", "razao social", "cpf cnpj", "cnpj"]}),
    "4.4": meta("search_filter", ["despesa"]),
    "4.5": meta("export", ["despesa"]),
    "4.6": meta("freshness", ["despesa"], referencia="mensal"),
    "4.7": meta("history", ["despesa"]),
    "4.8": sf({"beneficiario": ["beneficiari", "convenente", "favorecid", "entidade"], "objeto": ["objeto"],
               "valor": ["valor"]}, minimo=2, secoes=["transferencias"]),
    "4.9": sem("acordos e termos de cooperação firmados sem transferência de recursos: partes, objeto e obrigações",
               ["termo de cooperacao", "acordo de cooperacao", "protocolo de intencoes", "cooperacao tecnica"],
               secoes=["transferencias"]),
    "4.10": meta("search_filter", ["transferencias"]),
    "4.11": meta("export", ["transferencias"]),
    "4.12": meta("freshness", ["transferencias"], referencia="mensal"),
    "4.13": meta("history", ["transferencias"]),
    "5.1": sf({"nome": ["nome", "servidor"]}),
    "5.2": sf({"cargo": ["cargo", "funcao"]}),
    "5.3": sf({"lotacao": ["lotacao", "lotado", "orgao", "secretaria", "setor", "unidade"]}),
    "5.4": sf({"datas": ["admissao", "exoneracao", "inativacao", "nomeacao", "data de ingresso"]}),
    "5.5": sf({"carga_horaria": ["carga horaria", "jornada", "horas semanais"]}),
    "5.6": sf({"remuneracao": ["remuneracao", "salario", "vencimento", "bruto", "liquido", "proventos"]}),
    "5.7": doc(["tabela salarial", "tabela de vencimentos", "padrao remuneratorio", "plano de cargos",
                "tabela de remuneracao", "quadro de cargos"]),
    "5.8": meta("freshness", ["pessoal"], referencia="mensal"),
    "5.9": meta("history", ["pessoal"]),
    "5.10": meta("export", ["pessoal"]),
    "5.11": meta("search_filter", ["pessoal"]),
    "5.12": doc(["edital", "editais"], ["concurso", "processo seletivo", "selecao publica"]),
    "6.1": sf({"nome": ["nome", "beneficiari", "favorecid", "servidor"]}),
    "6.2": sf({"cargo": ["cargo", "funcao"]}),
    "6.3": sf({"quantidade": ["quantidade", "numero de diarias", "qtd", "qtde", "diarias"],
               "valor": ["valor"]}, minimo=1),
    "6.4": sf({"periodo": ["periodo", "data de saida", "data de retorno", "saida", "retorno", "inicio"]}),
    "6.5": sf({"motivo": ["motivo", "objetivo", "finalidade", "justificativa", "historico"]}),
    "6.6": sf({"destino": ["destino", "local", "cidade", "localidade"]}),
    "6.7": doc(["tabela de diarias", "valores das diarias", "valor da diaria", "decreto", "tabela"]),
    "6.8": meta("freshness", ["diarias"], referencia="mensal"),
    "6.9": meta("history", ["diarias"]),
    "6.10": meta("search_filter", ["diarias"]),
    "6.11": meta("export", ["diarias"]),
    "7.1": sf({"numero": ["numero", "processo", "n do", "nro"], "modalidade": ["modalidade"], "objeto": ["objeto"],
               "data": ["data", "abertura"], "valor": ["valor", "estimado", "homologado"],
               "situacao": ["situacao", "status", "fase"]}, minimo=4),
    "7.2": doc(["edital", "editais", "termo de referencia", "projeto basico"]),
    "7.3": doc(["dispensa"]),
    "7.4": doc(["inexigibilidade"]),
    "7.5": doc(["ata de registro", "atas de registro", "adesao", "srp", "registro de precos"]),
    "7.6": ex(["fracassada", "fracassadas", "deserta", "desertas"], regex=[r"\b(fracassad[ao]s?|desert[ao]s?)\b"]),
    "7.7": meta("search_filter", ["licitacoes"]),
    "7.8": meta("export", ["licitacoes"]),
    "7.9": meta("freshness", ["licitacoes"], referencia="mensal"),
    "7.10": meta("history", ["licitacoes"]),
    "8.1": sf({"contratado": ["contratad", "fornecedor", "empresa", "razao social", "credor"], "valor": ["valor"],
               "objeto": ["objeto"], "vigencia": ["vigencia", "inicio", "termino", "prazo"]}, minimo=3),
    "8.2": doc(["contrato", "contratos", "aditivo", "termo aditivo"]),
    "8.3": ex(["fiscal", "fiscal do contrato", "fiscais"], regex=[r"\bfisca(l|is)\b"]),
    "8.4": meta("freshness", ["contratos"], referencia="mensal"),
    "8.5": meta("history", ["contratos"]),
    "8.6": meta("export", ["contratos"]),
    "8.7": meta("search_filter", ["contratos"]),
    "8.8": ex(["ordem cronologica", "ordem cronologica de pagamentos"], secoes=["despesa", "contratos"],
              escopo="site"),
    "9.1": meta("history", ["patrimonio"], filtro=IMOV),
    "9.2": meta("freshness", ["patrimonio"], filtro=IMOV, referencia="anual"),
    "10.1": meta("history", ["patrimonio"], filtro=VEIC),
    "10.2": meta("freshness", ["patrimonio"], filtro=VEIC, referencia="anual"),
    "11.1": sf({"inicio": ["data de inicio", "inicio"], "etapa": ["etapa", "fase"],
                "percentual": ["percentual", "executado", "execucao", "%"], "status": ["situacao", "status"],
                "previsao": ["previsao", "conclusao", "termino"]}, minimo=3),
    "11.2": sf({"quantitativo": ["quantidade", "quantitativo"], "preco_unitario": ["preco unitario", "valor unitario"],
                "total": ["valor total", "total contratado", "valor contratado"]}, minimo=2),
    "11.3": sf({"executado": ["executad", "medid", "medicao"],
                "preco_praticado": ["valor pago", "preco praticado", "valor medido", "valor executado"]}, minimo=1),
    "11.4": ex(["obras paralisadas", "paralisada", "paralisadas"], regex=[r"\bparalisad[ao]s?\b"]),
    "12.1": doc(RGF),
    "12.2": meta("freshness", ["planejamento_contas", "rreo"], filtro=RGF, referencia="quadrimestral"),
    "12.3": meta("history", ["planejamento_contas", "rreo"], filtro=RGF),
    "12.4": meta("search_filter", ["planejamento_contas"]),
    "12.5": doc(["parecer previo", "prestacao de contas", "contas de governo", "contas anuais"]),
    "12.6": doc(["relatorio de gestao", "relatorio de atividades", "relatorio anual de gestao"]),
    "13.2": sf({"autor": ["autor", "parlamentar", "vereador", "deputado", "proponente"]}),
    "13.3": sf({"beneficiario": ["beneficiari", "municipio", "localidade", "bairro"]}),
    "13.4": sf({"objeto": ["objeto", "finalidade", "acao", "descricao"]}),
    "13.5": sf({"identificacao": ["numero da emenda", "codigo", "emenda", "numero"]}),
    "13.6": sf({"cronograma": ["cronograma", "inicio", "termino", "prazo", "execucao"]}),
    "13.7": sf({"instrumento": ["convenio", "contrato", "instrumento"]}),
    "13.8": sf({"valor": ["valor", "montante"]}),
    "14.1": ex(["servico de informacao ao cidadao", "sic", "acesso a informacao"],
               regex=[r"\b(setor|unidade|secretaria|controladoria|ouvidoria|coordenadoria|departamento)\b"]),
    "14.2": ex(["servico de informacao ao cidadao", "sic", "acesso a informacao"], regex=[R_ENDERECO]),
    "14.3": ex(["servico de informacao ao cidadao", "sic", "acesso a informacao"], regex=[R_HORARIO]),
    "14.4": ex(["servico de informacao ao cidadao", "sic", "acesso a informacao"], regex=[R_TELEFONE, R_EMAIL]),
    "15.1": ("canal_eletronico", {"tipo_url": ["esic"], "termos": ["e sic", "esic", "pedido de informacao",
                                                                   "solicitar informacao", "fala br", "falabr"],
                                  "secoes": ["sic"]}),
    "15.2": ("formulario_esic", {"secoes": ["sic"]}),
    "15.3": sem("norma municipal que regulamenta a Lei de Acesso à Informação (decreto ou lei local da LAI)",
                ["regulamenta a lei", "regulamentacao da lei de acesso", "lei 12 527", "lei n 12 527",
                 "decreto municipal", "lei de acesso a informacao"]),
    "15.4": ex(["prazo", "recurso", "autoridade"], regex=[r"\b(20|vinte)\b.{0,15}\bdias\b|\bprazo\b",
                                                          r"\brecurso"]),
    "15.5": doc(["relatorio estatistico", "relatorio anual", "estatisticas", "relatorio de pedidos",
                 "pedidos de acesso"]),
    "15.6": ex(["desclassificadas", "informacoes desclassificadas", "rol de informacoes desclassificadas"]),
    "15.7": ex(["informacoes classificadas", "documentos classificados", "grau de sigilo", "rol de informacoes classificadas"]),
    "16.1": ("accessibility", {"sinal": "breadcrumb"}),
    "16.2": ("accessibility", {"sinal": "alto_contraste"}),
    "16.3": ("accessibility", {"sinal": "ajuste_fonte"}),
    "16.4": ("accessibility", {"sinal": "simbolo"}),
    "16.5": ("pendente", {"motivo": "exige a ferramenta externa AccessMonitor (integração prevista)"}),
    "17.1": ex(["ouvidoria"], regex=[R_ENDERECO, R_TELEFONE + "|" + R_EMAIL, R_HORARIO]),
    "17.2": ex(["carta de servicos", "carta de servicos ao usuario"], escopo="site"),
    "17.3": ("canal_eletronico", {"tipo_url": ["ouvidoria"], "termos": ["ouvidoria", "fala br", "falabr",
                                                                        "manifestacao", "registrar manifestacao"],
                                  "secoes": ["ouvidoria"]}),
    "18.1": ex(["encarregado", "dpo", "protecao de dados", "lgpd", "dados pessoais"], regex=[R_EMAIL + "|" + R_TELEFONE]),
    "18.2": sem("serviços públicos solicitados e acessados por meio digital, sem atendimento presencial",
                ["servicos online", "servicos digitais", "servicos ao cidadao", "portal de servicos", "nota fiscal",
                 "iptu", "emissao", "segunda via", "agendamento"], secoes=["lgpd_digital"]),
    "18.3": ex(["dados abertos", "api", "licenca"], escopo="site"),
    "18.4": ex(["pesquisa de satisfacao", "avalie", "avaliacao do atendimento", "satisfacao"], escopo="site"),
    "19.1": doc(["plano plurianual", "ppa"]),
    "19.2": doc(["anexo", "anexos"], ["plano plurianual", "ppa"]),
    "19.3": doc(["diretrizes orcamentarias", "ldo"]),
    "19.4": doc(["anexo", "anexos", "metas fiscais", "riscos fiscais"], ["diretrizes orcamentarias", "ldo"]),
    "19.5": doc(["lei orcamentaria", "loa", "orcamento anual"]),
    "19.6": doc(["anexo", "anexos", "quadro de detalhamento"], ["lei orcamentaria", "loa"]),
    "19.7": sem("resultado do julgamento das contas do prefeito pela Câmara Municipal (decreto legislativo)",
                ["julgamento das contas", "decreto legislativo", "contas do prefeito", "contas de governo",
                 "camara municipal"], secoes=["planejamento_contas"]),
    "20.1": doc(RREO),
    "20.2": meta("freshness", ["rreo"], filtro=RREO, referencia="bimestral"),
    "20.3": meta("history", ["rreo"], filtro=RREO),
    "20.4": meta("search_filter", ["rreo"]),
    "20.5": doc(["balanco orcamentario"]),
    "20.6": doc(["balanco financeiro"]),
    "20.7": doc(["balanco patrimonial"]),
    "20.8": doc(["variacoes patrimoniais", "dvp"]),
    "20.9": doc(["fluxo de caixa", "dfc"]),
    "21.1": sem("serviços de saúde: unidades, horários, profissionais e especialidades",
                ["unidades de saude", "especialidades", "ubs", "profissionais de saude", "escala medica",
                 "horario de atendimento"]),
    "21.2": sem("estoque de medicamentos das farmácias públicas municipais",
                ["estoque de medicamentos", "remume", "farmacia", "medicamentos disponiveis"]),
    "21.3": sem("lista de espera de vagas em creches públicas e critérios de prioridade",
                ["lista de espera", "fila de creche", "creche", "vagas em creches", "educacao infantil"]),
    "21.4": ex(["divida ativa", "inscritos em divida ativa", "devedores"], regex=[r"\bd[ií]vida ativa\b"]),
}


def peso_de(codigo: str, fundamentacao: str, verificador: str) -> int:
    bloco = codigo.split(".")[0]
    if bloco in {"17", "18", "21"}:
        return 1
    if verificador in {"search_filter", "export", "freshness", "history"} and codigo != "1.3":
        return 2
    if "101" in fundamentacao or codigo in {"1.1", "1.2", "4.1", "5.1", "7.1", "8.1", "15.1"}:
        return 3
    return 2


def ler_planilha(caminho: Path) -> list[dict]:
    ws = openpyxl.load_workbook(caminho, data_only=True).active
    itens, fund_bloco, matriz = [], {}, "comum"
    for item, criterio, fund in ws.iter_rows(values_only=True):
        rotulo = str(item or "").strip()
        if "ESPECÍFICA" in rotulo.upper():
            matriz = "executivo"
            continue
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}", rotulo):
            continue
        bloco = rotulo.split(".")[0]
        fund = " ".join(str(fund or "").split())
        if fund:
            fund_bloco[bloco] = fund          # linhas sem fundamentação herdam a do bloco
        itens.append({"codigo": rotulo, "texto": " ".join(str(criterio).split()),
                      "fundamentacao": fund or fund_bloco.get(bloco, ""), "matriz": matriz})
    return itens


def montar(itens: list[dict]) -> list[dict]:
    faltando = [i["codigo"] for i in itens if i["codigo"] not in MAPA]
    if faltando:
        raise SystemExit(f"quesitos sem mapeamento: {faltando}")
    out = []
    for i in itens:
        verificador, params = MAPA[i["codigo"]]
        bloco = i["codigo"].split(".")[0]
        params = dict(params)
        if "secoes" not in params and verificador not in {"portal", "homepage_link", "accessibility", "pendente"} \
                and params.get("escopo") != "site":
            params["secoes"] = SECAO_BLOCO[bloco]
        peso = peso_de(i["codigo"], i["fundamentacao"], verificador)
        out.append({
            "codigo": i["codigo"], "bloco": bloco, "dimensao": DIMENSAO[bloco], "matriz": i["matriz"],
            "texto": i["texto"], "fundamentacao": i["fundamentacao"], "peso": peso, "essencial": peso == 3,
            "aplica_a": ["executivo_municipal"] if i["codigo"] == "21.3" else [i["matriz"]],
            "onda": ONDA.get(i["codigo"]), "verificador": verificador, "params": params,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(CFG / "checklistPortalTransp.xlsx"))
    ap.add_argument("--saida", default=str(CFG / "criteria.yaml"))
    a = ap.parse_args()
    criterios = montar(ler_planilha(Path(a.xlsx)))
    corpo = yaml.safe_dump({"criterios": criterios}, allow_unicode=True, sort_keys=False, width=120)
    sha = hashlib.sha256(Path(a.xlsx).read_bytes()).hexdigest()[:12]
    cab = ("# Gerado por scripts/importar_checklist.py a partir de checklistPortalTransp.xlsx "
           f"(sha256 {sha}).\n# Fonte de verdade da Camada 2: ajuste termos, sinônimos e pesos aqui.\n"
           "# verificador + params definem COMO cada quesito é avaliado (ver compliance/checkers).\n\n")
    Path(a.saida).write_text(cab + corpo, encoding="utf-8")
    from collections import Counter
    print(f"{len(criterios)} quesitos -> {a.saida}")
    print("por matriz:", dict(Counter(c["matriz"] for c in criterios)))
    print("por verificador:", dict(Counter(c["verificador"] for c in criterios)))
    print("por peso:", dict(Counter(c["peso"] for c in criterios)))


if __name__ == "__main__":
    main()
