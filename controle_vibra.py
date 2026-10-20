"""
Extrator do controle de NFs emitidas para a Vibra Energia.

Monta, direto do DW (BigQuery), a lista de notas da Vibra com valor, pedido,
material, situação do título (em aberto, pago, devolvido, cancelado...) e
grava na planilha "PENDENCIAS VIBRA" (aba "Controle NFs Vibra"), onde o time
de Emissão confirma a entrega e o Financeiro anota a cobrança.

Divisão de responsabilidade na planilha:
- colunas A a N ("Do CIGAM") são do extrator: reescritas a cada execução,
  ninguém edita à mão;
- coluna O (cliente final) é fórmula da planilha: procura a filial Vibra +
  o nº do local de entrega da NF na aba "Locais de entrega", mantida pelo
  time; local novo é acrescentado lá em branco pelo extrator;
- colunas P a S (status de entrega, data, observações) são do time: o
  extrator lê o que já está lá antes de reescrever a aba e devolve cada
  valor na linha da mesma NF (chave = NF + série);
- colunas T e U (dias em atraso, próxima ação) são fórmulas da planilha.

Roda de dois jeitos:
- dentro da própria planilha, pelo Apps Script apps_script/ControleVibra.gs
  (menu "Controle Vibra"), com o login de quem executa, sem service
  account. É o espelho deste módulo: mesmo SQL e mesmas regras, conferidos
  por tests/test_apps_script.py. Ao mudar uma regra aqui, mude lá também;
- pelo terminal, pra agendar (cron, GitHub Actions etc.) ou usar a partir de
  outro programa (preparar_controle / sincronizar_planilha):
      python controle_vibra.py --credenciais sa.json
      python controle_vibra.py --credenciais sa.json --somente-ler   # só mostra

Pelo terminal, a service account precisa de leitura no BigQuery (bronze.cigam__empresas,
bronze.cigam__notas_fiscais, silver.lancamentos_receber,
silver.cadastro_materiais_cigam) e de acesso de EDITOR à planilha
(compartilhar a planilha com o e-mail da service account).

A lógica fica testável sem BigQuery nem planilha (tests/test_controle_vibra.py).
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ID = "hip-bonito-453017-m2"
PLANILHA_ID_PADRAO = "1aRa2nw8LfgJxAGQp7qPp0wudnzCxGEgxSAEL2W7080s"
ABA_CONTROLE = "Controle NFs Vibra"
ABA_RESUMO = "Resumo"
ABA_LISTAS = "Listas"
ABA_FONTES = "Fontes"
ABA_LOCAIS = "Locais de entrega"
# aba opcional pra trazer pra dentro do controle o que o operacional anotou
# fora dele (NF, Série + colunas com o nome das colunas manuais): preenche
# só o que está em branco no controle e é limpa depois de usada
ABA_IMPORTACAO = "Importar operacional"

# Vibra Energia é reconhecida pela raiz do CNPJ, não pela divisão do
# cadastro: das 6 filiais com raiz 34274233 só uma está na divisão
# 12 VIBRA, as outras 5 estão em 10 CLIENTES.
RAIZ_CNPJ_VIBRA = "34274233"

# piso fixo da consulta: a lista final é filtrada em Python (ver
# selecionar_linhas), mas uma NF antiga que ainda está na planilha, ou que
# segue em aberto, precisa vir do DW mesmo emitida antes do "desde"
PISO_CONSULTA = date(2024, 1, 1)
DESDE_PADRAO = date(2026, 1, 1)

FUSO = ZoneInfo("America/Sao_Paulo")

# --- status financeiro (coluna K) -------------------------------------------
STATUS_EM_ABERTO = "Em aberto"
STATUS_PAGO_PARCIAL = "Pago parcial"
STATUS_PAGO = "Pago"
STATUS_DEVOLVIDA = "Devolvida"
STATUS_ABATIDA = "Abatida"
STATUS_CANCELADA = "Cancelada"
STATUS_PERDA = "Baixa por perda"
STATUS_SEM_TITULO = "Sem título"
STATUS_ABERTOS = (STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL, STATUS_SEM_TITULO)

SIGNIFICADO_STATUS = [
    (STATUS_EM_ABERTO, "Título sem nenhuma baixa no CIGAM."),
    (STATUS_PAGO_PARCIAL, "Título com parte do valor recebida; o saldo segue em aberto."),
    (STATUS_PAGO, "Título liquidado por recebimento."),
    (STATUS_DEVOLVIDA, "Título baixado por abatimento (portador X30) e com NF de devolução da Vibra."),
    (STATUS_ABATIDA, "Título baixado por abatimento (portador X30), sem NF de devolução."),
    (STATUS_CANCELADA, "NF cancelada no CIGAM."),
    (STATUS_PERDA, "Título baixado por perda (portadores X90 a X99)."),
    (STATUS_SEM_TITULO, "NF confirmada sem título a receber no DW (o DW atualiza com até 1 dia de atraso)."),
]

# --- status de entrega (preenchido pela Emissão/operacional) ------------------
# esteira do operacional: entrega -> instalação -> comodato da Vibra assinado
# pelo cliente final. O Financeiro só cobra a Vibra com o comodato assinado.
STATUS_ENTREGA_ACAO = [
    ("A validar", "Emissão: confirmar entrega"),
    ("Não entregue", "Emissão: acompanhar entrega"),
    ("Não instalado", "Emissão: acompanhar instalação"),
    ("Não instalado, comodato assinado", "Financeiro: cobrar Vibra"),
    ("Aguardando comodato da Vibra", "Emissão: cobrar comodato da Vibra"),
    ("Aguardando cliente assinar comodato", "Emissão: obter assinatura do comodato"),
    ("Comodato assinado", "Financeiro: cobrar Vibra"),
    ("Problema na entrega", "Emissão: resolver entrega"),
    ("Não se aplica", "Financeiro: cobrar Vibra"),
]
# material já entregue (base dos filtros "entregues" e da visão da Vibra)
STATUS_ENTREGUES = ["Não instalado", "Não instalado, comodato assinado", "Aguardando comodato da Vibra",
                    "Aguardando cliente assinar comodato", "Comodato assinado", "Não se aplica"]
# ainda tem etapa do operacional (filtro "Aguardando a Emissão")
STATUS_PENDENTES_OPERACIONAL = ["A validar", "Não entregue", "Não instalado", "Não instalado, comodato assinado",
                                "Aguardando comodato da Vibra", "Aguardando cliente assinar comodato",
                                "Problema na entrega"]
# status da lista anterior -> etapa equivalente da esteira
MIGRACAO_STATUS_ENTREGA = {
    "Aguardando entrega": "Não entregue",
    "Aguardando NF assinada": "Aguardando cliente assinar comodato",
    "NF assinada recebida": "Comodato assinado",
}
ACAO_SEM_TITULO = "Financeiro: conferir título no CIGAM"
ACAO_SEM_STATUS = "Preencher status entrega"
ACAO_ENCERRADA = "Encerrada"
LOCAL_SEM_CADASTRO = "Cadastrar na aba Locais de entrega"

# --- layout da aba de controle ----------------------------------------------
COLUNAS_DW = [
    "NF", "Série", "Emissão", "Vencimento", "Valor (R$)", "Saldo em aberto (R$)",
    "Nº Pedido Vibra", "Filial Vibra (CNPJ)", "Tipo de NF", "Material",
    "Status financeiro", "Data pagamento / baixa", "NF de devolução", "Local de entrega (nº)",
    "NF de remessa",
]
# cliente da NF de remessa: não vira coluna, preenche a aba Locais de entrega
COLUNAS_INTERNAS = ["_cliente_remessa", "_codigo_remessa"]
# o cliente final é fórmula na planilha (procura filial + nº do local na aba
# Locais de entrega), pra que um local cadastrado lá apareça na hora em
# todas as NFs, sem depender de rodar o extrator nem de alguém digitar
COLUNA_CLIENTE_FINAL = "Cliente final"
COLUNAS_EMISSAO = ["Status entrega", "NF comodato Vibra", "Situação NF assinada", "Data entrega / NF assinada",
                   "Obs. Emissão"]
COLUNAS_FINANCEIRO = ["Obs. financeiro"]
COLUNAS_MANUAIS = COLUNAS_EMISSAO + COLUNAS_FINANCEIRO
COLUNAS_FORMULA = ["Dias em atraso", "Próxima ação"]
COLUNAS = COLUNAS_DW + [COLUNA_CLIENTE_FINAL] + COLUNAS_MANUAIS + COLUNAS_FORMULA
# filtro rápido na linha 1 (congelada, fica sempre à vista): lista em A1:D1
# e período em E1 / F1. Quem aplica é o onEdit do apps_script/FiltrosVibra.gs;
# os rótulos têm que ser os mesmos de lá (conferido em tests/test_apps_script.py)
OPCOES_FILTRO_RAPIDO = ["Todas as NFs", "Atualizar o controle da Vibra", "Pendentes de pagamento",
                        "Pendentes entregues", "Pendentes entregues em atraso", "Aguardando a Emissão", "Pagos",
                        "Pagos no período", "Canceladas / devolvidas"]
COLUNAS_FILTRO_RAPIDO = 4          # A a D, mescladas
COLUNA_PERIODO_DE = 4              # E
COLUNA_PERIODO_ATE = 5             # F
COLUNA_TITULO = 6                  # G: "Do CIGAM (...)" vai de G até o cliente final
# coluna que a Emissão preenchia à mão antes de virar fórmula: o que foi
# digitado e não bate com a aba Locais de entrega vai pra Obs. Emissão
COLUNA_CLIENTE_FINAL_DIGITADO = "Cliente final / local de entrega"


def _letra(nome_coluna: str) -> str:
    indice = COLUNAS.index(nome_coluna)
    letra = ""
    indice += 1
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letra = chr(65 + resto) + letra
    return letra


ULTIMA_COLUNA = _letra(COLUNAS[-1])
LINHA_CABECALHO = 2
PRIMEIRA_LINHA_DADOS = 3


# ============================================================================
# 1. Consulta ao DW
# ============================================================================
SQL_NOTAS_VIBRA = f"""
WITH vibra AS (
  SELECT codigo, REGEXP_REPLACE(cnpjCpf, r'\\D', '') AS cnpj
  FROM `{PROJECT_ID}.bronze.cigam__empresas`
  WHERE STARTS_WITH(REGEXP_REPLACE(cnpjCpf, r'\\D', ''), @raiz_cnpj)
),
-- NF de saída (N = normal, E = entrega futura). especieNota S = saída
-- válida, N = nula (cancelada); a nula só entra se chegou a ter título.
notas AS (
  SELECT n.nf, n.serie, n.cliente, n.dataEmissao, n.status, n.especieNota,
         NULLIF(TRIM(n.dataCancelamento), '') AS dataCancelamento,
         -- "Local de Entrega" da aba Transporte da NF: o CIGAM guarda o nº
         -- como um caractere em sequência a partir do "!" (! = 1, " = 2,
         -- # = 3...), pulando ' * ? e \\ (não aparecem em NF nenhuma). Então
         -- "(" = 7, ")" = 8, "+" = 9, "," = 10. Conferido com a lista de
         -- endereços da Vibra RJ: a Reiter (cadastrada em 01/10/2026) é a 10ª
         -- e só aparece nas NFs de outubro, com ",".
         -- O nº aponta pra lista de endereços de entrega do cliente (a
         -- filial da Vibra), que não está no DW: o nome do cliente final
         -- vem da aba "Locais de entrega" da planilha.
         IF(TRIM(IFNULL(n.ordemEnderecoEntrega, '')) = '', NULL,
            UNICODE(n.ordemEnderecoEntrega) - 32
            - (SELECT COUNT(*) FROM UNNEST([39, 42, 63, 92]) AS pulado
               WHERE pulado < UNICODE(n.ordemEnderecoEntrega))) AS localEntrega,
         SAFE_CAST(n.totalNF AS NUMERIC) AS totalNF, n.itensNf_json,
         NULLIF(TRIM(n.ordemCompra), '') AS pedidoRemessa
  FROM `{PROJECT_ID}.bronze.cigam__notas_fiscais` n
  WHERE n.dataEmissao >= @piso AND n.cliente IN (SELECT codigo FROM vibra)
    AND n.especieNota IN ('S', 'N') AND n.tipoNota IN ('E', 'N')
),
-- venda triangular: a NF da Vibra (CFOP 6119/6118, venda à ordem) só fatura;
-- o material vai com a NF de remessa por conta e ordem (CFOP 5923/6923) pro
-- cliente final, que é a que ele assina. A NF da Vibra guarda em ordemCompra
-- o pedido interno da remessa (ex.: NF 25263 -> pedido 009525 -> NF 25265).
remessas AS (
  SELECT TRIM(pedido) AS pedido, STRING_AGG(DISTINCT nf, ', ' ORDER BY nf) AS nfs_remessa,
         ANY_VALUE(cliente) AS cliente_remessa
  FROM `{PROJECT_ID}.bronze.cigam__notas_fiscais`
  WHERE dataEmissao >= @piso AND especieNota = 'S' AND CFOP IN ('5923', '6923')
  GROUP BY 1
),
-- NF de entrada (D = devolução, C = complementar) que a Vibra emite
-- referenciando a nossa NF: é assim que um "cancelamento" depois de 24 h
-- aparece no CIGAM (ex.: NF 22114 devolvida pela 23920).
devolucoes AS (
  SELECT notaReferencia AS nf, serieReferencia AS serie, cliente,
         STRING_AGG(nf, ', ' ORDER BY nf) AS nfs_devolucao,
         MAX(dataEmissao) AS data_devolucao
  FROM `{PROJECT_ID}.bronze.cigam__notas_fiscais`
  WHERE dataEmissao >= @piso AND cliente IN (SELECT codigo FROM vibra)
    AND especieNota = 'E' AND tipoNota IN ('D', 'C') AND status = '3'
  GROUP BY 1, 2, 3
),
lanc AS (
  SELECT nf, serie, codigoEmpresa, codigoTipo, codigoLancamento, codigoPartida, codigoPortador,
         SAFE.PARSE_DATE('%d/%m/%Y', dataVencimento) AS vencimento,
         SAFE.PARSE_DATE('%d/%m/%Y', data) AS data,
         SAFE_CAST(valor AS NUMERIC) AS valor
  FROM `{PROJECT_ID}.silver.lancamentos_receber`
  WHERE codigoEmpresa IN (SELECT codigo FROM vibra)
),
-- a baixa (E) aponta pro título pelo codigoPartida. O saldo da linha R
-- NÃO serve: ela não é reextraída depois da baixa e fica com saldo cheio
-- (ex.: NF 12121, paga em 27/02/2026 e com R ainda "A" e saldo 7.851,02).
baixas AS (
  SELECT codigoPartida,
         SUM(IF(codigoPortador = 'X30', valor, 0)) AS abatido,
         SUM(IF(REGEXP_CONTAINS(codigoPortador, r'^X9[0-9]$'), valor, 0)) AS perda,
         SUM(IF(codigoPortador = 'X30' OR REGEXP_CONTAINS(codigoPortador, r'^X9[0-9]$'), 0, valor)) AS pago,
         MAX(data) AS data_baixa
  FROM lanc WHERE codigoTipo = 'E'
  GROUP BY 1
),
titulos AS (
  SELECT t.nf, t.serie, t.codigoEmpresa,
         MIN(t.vencimento) AS vencimento,
         SUM(t.valor) AS valor_titulo,
         IFNULL(SUM(b.pago), 0) AS pago,
         IFNULL(SUM(b.abatido), 0) AS abatido,
         IFNULL(SUM(b.perda), 0) AS perda,
         MAX(b.data_baixa) AS data_baixa
  FROM lanc t
  LEFT JOIN baixas b ON b.codigoPartida = t.codigoLancamento
  WHERE t.codigoTipo IN ('R', 'c')
  GROUP BY 1, 2, 3
),
itens AS (
  SELECT n.nf, n.serie, n.cliente,
         TRIM(JSON_VALUE(i, '$.codigoMaterial')) AS material,
         SAFE_CAST(JSON_VALUE(i, '$.quantidade') AS NUMERIC) AS quantidade,
         SAFE_CAST(JSON_VALUE(i, '$.quantidade') AS NUMERIC)
           * SAFE_CAST(JSON_VALUE(i, '$.precoUnitario') AS NUMERIC) AS valor,
         JSON_VALUE(i, '$.descricao') AS texto
  FROM notas n, UNNEST(JSON_QUERY_ARRAY(n.itensNf_json)) i
),
-- o nº do pedido da Vibra (10 dígitos, 45... ou 46...) não tem campo
-- próprio no CIGAM: vem digitado no texto do item ("OC: 4503816077",
-- "Contrato CTA SMART nº 4600213963")
itens_nf AS (
  SELECT i.nf, i.serie, i.cliente,
         STRING_AGG(CONCAT(FORMAT('%g', CAST(i.quantidade AS FLOAT64)), 'x ', IFNULL(m.descricao, i.material)),
                    ' + ' ORDER BY i.valor DESC) AS material,
         LOGICAL_OR(m.descricao_subgrupo = 'TOTEM') AS tem_cta,
         LOGICAL_OR(m.descricao_subgrupo = 'SONDA') AS tem_sonda,
         LOGICAL_OR(STARTS_WITH(i.material, '900001')) AS tem_licenciamento,  -- grupo 900, subgrupo 001
         STRING_AGG(IFNULL(i.texto, ''), ' ') AS textos
  FROM itens i
  LEFT JOIN `{PROJECT_ID}.silver.cadastro_materiais_cigam` m ON m.codigo_material = i.material
  GROUP BY 1, 2, 3
)
SELECT n.nf, n.serie, n.cliente, v.cnpj, n.dataEmissao AS emissao, n.status,
       n.dataCancelamento AS data_cancelamento, n.totalNF AS total_nf,
       t.vencimento, t.valor_titulo, t.pago, t.abatido, t.perda, t.data_baixa,
       d.nfs_devolucao, d.data_devolucao, n.localEntrega AS local_entrega,
       i.material, i.tem_cta, i.tem_sonda, i.tem_licenciamento, i.textos,
       r.nfs_remessa AS nf_remessa, r.cliente_remessa, e.nomeCompleto AS nome_remessa,
       e.municipio AS municipio_remessa, e.uf AS uf_remessa
FROM notas n
JOIN vibra v ON v.codigo = n.cliente
LEFT JOIN remessas r ON r.pedido = n.pedidoRemessa
LEFT JOIN `{PROJECT_ID}.bronze.cigam__empresas` e ON e.codigo = r.cliente_remessa
LEFT JOIN titulos t ON t.nf = n.nf AND t.serie = n.serie AND t.codigoEmpresa = n.cliente
LEFT JOIN devolucoes d ON d.nf = n.nf AND d.serie = n.serie AND d.cliente = n.cliente
LEFT JOIN itens_nf i ON i.nf = n.nf AND i.serie = n.serie AND i.cliente = n.cliente
WHERE n.especieNota = 'S' OR t.nf IS NOT NULL
"""

TABELAS_CONSULTADAS = [
    "bronze.cigam__empresas", "bronze.cigam__notas_fiscais",
    "silver.lancamentos_receber", "silver.cadastro_materiais_cigam",
]


def buscar_notas_vibra(client_bq, piso: date = PISO_CONSULTA) -> pd.DataFrame:
    """Uma linha por NF de saída da Vibra emitida desde `piso`, com título,
    baixas, devolução e itens já agregados. Sem permissão numa das tabelas,
    o BigQuery devolve 403 e a mensagem diz quais tabelas a service account
    precisa ler."""
    from google.api_core.exceptions import Forbidden
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("raiz_cnpj", "STRING", RAIZ_CNPJ_VIBRA),
        bigquery.ScalarQueryParameter("piso", "DATE", piso),
    ])
    try:
        return client_bq.query(SQL_NOTAS_VIBRA, job_config=job_config).to_dataframe(create_bqstorage_client=False)
    except Forbidden as erro:
        raise PermissionError(
            "A service account não tem leitura em alguma tabela do controle Vibra. "
            f"Ela precisa ler: {', '.join(TABELAS_CONSULTADAS)}. Detalhe do BigQuery: {erro.message}"
        ) from erro


# ============================================================================
# 2. Regras: status financeiro, tipo de NF, pedido
# ============================================================================
TOLERANCIA_CENTAVOS = 0.01


def _vazio(valor) -> bool:
    if valor is None:
        return True
    try:
        return bool(pd.isna(valor))
    except (TypeError, ValueError):
        return False


def _str(valor) -> str:
    """Texto vindo do DataFrame do BigQuery. No pandas 3 coluna de texto
    vazia vem NaN, não None, e `str(nan or "")` vira "nan"."""
    return "" if _vazio(valor) else str(valor).strip()


def _numero(valor) -> float:
    numero = pd.to_numeric(valor, errors="coerce")
    return 0.0 if pd.isna(numero) else float(numero)


def _data(valor):
    """Converte o que vier do BigQuery (date, Timestamp, 'dd/mm/aaaa') em date."""
    if _vazio(valor):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto or texto.lower() == "null":
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def classificar_status(linha) -> tuple:
    """(status financeiro, data do status, saldo em aberto) de uma NF.

    A ordem importa: NF cancelada vence tudo; depois, sem título, não há o
    que olhar; com título, o saldo é valor menos TODAS as baixas (pagamento,
    abatimento e perda). Título zerado só por abatimento é devolução quando
    existe NF de devolução/complementar da Vibra referenciando a nossa NF —
    é o caso da 22114 (devolvida pela 23920, abatida em 03/06/2026)."""
    if _str(linha.get("status")) == "5":
        return STATUS_CANCELADA, _data(linha.get("data_cancelamento")), 0.0

    valor_titulo = linha.get("valor_titulo")
    if _vazio(valor_titulo):
        return STATUS_SEM_TITULO, None, None

    valor_titulo = _numero(valor_titulo)
    pago = _numero(linha.get("pago"))
    abatido = _numero(linha.get("abatido"))
    perda = _numero(linha.get("perda"))
    baixado = pago + abatido + perda
    saldo = round(max(valor_titulo - baixado, 0.0), 2)
    data_baixa = _data(linha.get("data_baixa"))

    if saldo > TOLERANCIA_CENTAVOS:
        if baixado > TOLERANCIA_CENTAVOS:
            return STATUS_PAGO_PARCIAL, data_baixa, saldo
        return STATUS_EM_ABERTO, None, saldo
    if perda > TOLERANCIA_CENTAVOS:
        return STATUS_PERDA, data_baixa, 0.0
    if pago > TOLERANCIA_CENTAVOS:
        return STATUS_PAGO, data_baixa, 0.0
    if _str(linha.get("nfs_devolucao")):
        return STATUS_DEVOLVIDA, _data(linha.get("data_devolucao")) or data_baixa, 0.0
    return STATUS_ABATIDA, data_baixa, 0.0


def _inteiro(valor):
    numero = pd.to_numeric(_str(valor), errors="coerce")
    return None if pd.isna(numero) else int(numero)


def _verdadeiro(valor) -> bool:
    """LOGICAL_OR do BigQuery volta NULL (pd.NA) pra NF sem item, e
    bool(pd.NA) levanta erro."""
    return False if _vazio(valor) else bool(valor)


def classificar_tipo(linha) -> str:
    """Tipo de NF pelos itens: série S/S1 é serviço (mensalidade quando tem
    licenciamento); série de produto é venda de CTA quando tem totem,
    senão venda de sonda, senão peças e periféricos."""
    serie = _str(linha.get("serie")).upper()
    if serie.startswith("S"):
        return "Mensalidade" if _verdadeiro(linha.get("tem_licenciamento")) else "Serviço"
    if _verdadeiro(linha.get("tem_cta")):
        return "Venda de CTA"
    if _verdadeiro(linha.get("tem_sonda")):
        return "Venda de Sonda"
    return "Peças e periféricos"


_RE_PEDIDO_VIBRA = re.compile(r"(?<!\d)(4[56]\d{8})(?!\d)")


def extrair_pedido_vibra(textos) -> str:
    achados = list(dict.fromkeys(_RE_PEDIDO_VIBRA.findall(_str(textos))))
    return ", ".join(achados)


def formatar_cnpj(digitos) -> str:
    digitos = re.sub(r"\D", "", _str(digitos))
    if len(digitos) != 14:
        return digitos
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def montar_controle(df_dw: pd.DataFrame) -> pd.DataFrame:
    """Aplica as regras sobre o retorno de buscar_notas_vibra. Uma linha
    por NF, com as colunas do DW já no formato da planilha (datas como
    date, valores como float)."""
    linhas = []
    for _, bruto in df_dw.iterrows():
        linha = bruto.to_dict()
        status, data_status, saldo = classificar_status(linha)
        total_nf = _numero(linha.get("total_nf")) or _numero(linha.get("valor_titulo"))
        linhas.append({
            "NF": _str(linha.get("nf")),
            "Série": _str(linha.get("serie")),
            "Emissão": _data(linha.get("emissao")),
            "Vencimento": _data(linha.get("vencimento")),
            "Valor (R$)": round(total_nf, 2),
            "Saldo em aberto (R$)": saldo,
            "Nº Pedido Vibra": extrair_pedido_vibra(linha.get("textos")),
            "Filial Vibra (CNPJ)": formatar_cnpj(linha.get("cnpj")),
            "Tipo de NF": classificar_tipo(linha),
            "Material": _str(linha.get("material")),
            "Status financeiro": status,
            "Data pagamento / baixa": data_status,
            "NF de devolução": _str(linha.get("nfs_devolucao")),
            "Local de entrega (nº)": _inteiro(linha.get("local_entrega")),
            "NF de remessa": _str(linha.get("nf_remessa")),
            "_cliente_remessa": nome_cliente_remessa(linha.get("nome_remessa"), linha.get("municipio_remessa"),
                                                     linha.get("uf_remessa")),
            "_codigo_remessa": _str(linha.get("cliente_remessa")),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_DW + COLUNAS_INTERNAS)


def nome_cliente_remessa(nome, municipio, uf) -> str:
    """"CIA. DE FERRO LIGAS DA BAHIA - FERBASA (Pojuca/BA)": o cliente da NF
    de remessa no formato da aba Locais de entrega."""
    nome, municipio, uf = _str(nome), _str(municipio), _str(uf)
    if not nome:
        return ""
    cidade = " ".join(p[:1].upper() + p[1:].lower() for p in municipio.split())
    local = "/".join(x for x in (cidade, uf.upper()) if x)
    return f"{nome} ({local})" if local else nome


# ============================================================================
# 3. Mescla com o que o time já preencheu na planilha
# ============================================================================
def chave_nf(nf, serie) -> tuple:
    """NF + série identifica a nota: o mesmo número existe na série 2
    (produto) e na S1 (serviço), pra clientes diferentes."""
    nf = re.sub(r"\D", "", _str(nf))
    nf = nf.lstrip("0") or nf
    return nf, _str(serie).upper()


def ler_preenchimento_manual(valores_aba: list) -> dict:
    """Lê os valores exibidos da aba de controle (get_all_values) e devolve
    {(nf, série): {coluna manual: valor}}. Acha o cabeçalho pela linha cuja
    primeira célula é "NF", e as colunas pelo nome, então também lê a aba
    no layout anterior ao extrator. Uma linha sem série no layout antigo
    não tem como casar e é ignorada."""
    if not valores_aba:
        return {}
    indice_cabecalho = next(
        (i for i, linha in enumerate(valores_aba[:10]) if linha and str(linha[0]).strip() == "NF"), None)
    if indice_cabecalho is None:
        return {}
    cabecalho = [str(c).strip() for c in valores_aba[indice_cabecalho]]
    lidas = COLUNAS_MANUAIS + ["Nº Pedido Vibra", COLUNA_CLIENTE_FINAL_DIGITADO]
    posicoes = {nome: cabecalho.index(nome) for nome in lidas + ["Série"] if nome in cabecalho}
    if "Série" not in posicoes:
        return {}

    manuais = {}
    for linha in valores_aba[indice_cabecalho + 1:]:
        if not linha or not str(linha[0]).strip():
            continue
        def celula(nome):
            pos = posicoes.get(nome)
            return str(linha[pos]).strip() if pos is not None and pos < len(linha) else ""
        chave = chave_nf(linha[0], celula("Série"))
        manuais[chave] = {nome: celula(nome) for nome in lidas}
    return manuais


def status_entrega_inicial(linha) -> str:
    """Status de entrega de uma NF que entra agora na planilha: serviço não
    tem entrega física; venda aberta começa em "A validar" pra Emissão
    confirmar; NF já encerrada (paga, devolvida...) fica em branco."""
    if str(linha["Série"]).upper().startswith("S"):
        return "Não se aplica"
    if linha["Status financeiro"] in STATUS_ABERTOS:
        return "A validar"
    return ""


def selecionar_linhas(controle: pd.DataFrame, manuais: dict, desde: date) -> pd.DataFrame:
    """Entra na planilha: NF emitida desde `desde`, NF ainda em aberto
    (qualquer data) e NF que já está na planilha (pra não sumir com o que o
    time anotou). Ordem: abertas primeiro, por vencimento; depois as
    encerradas, da mais recente pra mais antiga."""
    if controle.empty:
        return controle
    chaves = controle.apply(lambda l: chave_nf(l["NF"], l["Série"]), axis=1)
    emissao = pd.to_datetime(controle["Emissão"], errors="coerce")
    manter = (
        (emissao >= pd.Timestamp(desde))
        | controle["Status financeiro"].isin(STATUS_ABERTOS)
        | chaves.isin(set(manuais))
    )
    selecionado = controle[manter].copy()
    selecionado["_aberta"] = selecionado["Status financeiro"].isin(STATUS_ABERTOS)
    selecionado["_venc"] = pd.to_datetime(selecionado["Vencimento"], errors="coerce")
    selecionado["_emis"] = pd.to_datetime(selecionado["Emissão"], errors="coerce")
    abertas = selecionado[selecionado["_aberta"]].sort_values(["_venc", "_emis"], na_position="last")
    fechadas = selecionado[~selecionado["_aberta"]].sort_values("_emis", ascending=False)
    return pd.concat([abertas, fechadas]).drop(columns=["_aberta", "_venc", "_emis"]).reset_index(drop=True)


CABECALHO_LOCAIS = ["Filial Vibra (CNPJ)", "Local de entrega (nº)", "Cliente final",
                    "Cód. CIGAM do cliente final", "Origem"]


def chave_local(cnpj, numero):
    """O nº do local de entrega é por cadastro de cliente: o local 4 da Vibra
    Santo André (Louis Dreyfus) não é o local 4 da Vibra BA (Ferbasa)."""
    digitos = re.sub(r"\D", "", _str(cnpj))
    numero = _inteiro(numero)
    return None if not digitos or numero is None else (digitos, numero)


def ler_locais_entrega(valores_aba: list) -> dict:
    """{(CNPJ da filial, nº do local): cliente final} da aba Locais de
    entrega, mantida pelo time. Local listado sem cliente final vem com ""."""
    indice = next((i for i, linha in enumerate(valores_aba[:10] if valores_aba else [])
                   if linha and _str(linha[0]) == CABECALHO_LOCAIS[0]), None)
    if indice is None:
        return {}
    locais = {}
    for linha in valores_aba[indice + 1:]:
        linha = list(linha) + [""] * (3 - len(linha))
        chave = chave_local(linha[0], linha[1])
        if chave is not None:
            locais[chave] = _str(linha[2])
    return locais


def locais_sem_cadastro(tabela: pd.DataFrame, locais: dict) -> list:
    """Linhas pra acrescentar na aba Locais de entrega: cada (filial, nº)
    que aparece nas NFs e ainda não está lá. O cliente final vem do cliente
    da NF de remessa (venda triangular); sem remessa, fica em branco pra
    alguém preencher, com as NFs onde o local apareceu."""
    vistos = {}
    for _, linha in tabela.iterrows():
        chave = chave_local(linha["Filial Vibra (CNPJ)"], linha["Local de entrega (nº)"])
        if chave is None or chave in locais:
            continue
        visto = vistos.setdefault(chave, {"cnpj": linha["Filial Vibra (CNPJ)"], "nfs": [], "remessas": [],
                                          "cliente": "", "codigo": ""})
        visto["nfs"].append(str(linha["NF"]))
        cliente = _str(linha.get("_cliente_remessa"))
        if cliente and not visto["cliente"]:
            visto["cliente"], visto["codigo"] = cliente, _str(linha.get("_codigo_remessa"))
        if _str(linha.get("NF de remessa")):
            visto["remessas"].append(_str(linha.get("NF de remessa")))
    ordem = lambda nf: int(nf) if nf.isdigit() else 0
    linhas = []
    for chave in sorted(vistos):
        v = vistos[chave]
        nfs = ", ".join(sorted(v["nfs"], key=ordem))
        if v["cliente"]:
            remessas = ", ".join(sorted(v["remessas"], key=ordem))
            origem = f"Cliente da NF de remessa {remessas} (preenchido pelo extrator)"
        else:
            origem = f"Preencher: visto na NF {nfs}" if "," not in nfs else f"Preencher: visto nas NFs {nfs}"
        linhas.append([_texto(v["cnpj"]), chave[1], v["cliente"], _texto(v["codigo"]), origem])
    return linhas


def ler_importacao(valores_aba: list) -> dict:
    """{(nf, série): {coluna manual: valor}} da aba Importar operacional.
    Acha o cabeçalho pela linha cuja primeira célula é "NF"; só lê colunas
    com o nome de uma coluna manual ou do nº do pedido."""
    indice = next((i for i, linha in enumerate(valores_aba[:10] if valores_aba else [])
                   if linha and _str(linha[0]) == "NF"), None)
    if indice is None:
        return {}
    cabecalho = [_str(c) for c in valores_aba[indice]]
    if "Série" not in cabecalho:
        return {}
    serie = cabecalho.index("Série")
    colunas = {nome: cabecalho.index(nome) for nome in COLUNAS_MANUAIS + ["Nº Pedido Vibra"] if nome in cabecalho}
    importados = {}
    for linha in valores_aba[indice + 1:]:
        if not linha or not _str(linha[0]):
            continue
        celula = lambda pos: _str(linha[pos]) if pos < len(linha) else ""
        valores = {nome: celula(pos) for nome, pos in colunas.items() if celula(pos)}
        if valores:
            importados[chave_nf(linha[0], celula(serie))] = valores
    return importados


def mesclar_manuais(selecionado: pd.DataFrame, manuais: dict, locais: dict = None,
                    importados: dict = None) -> pd.DataFrame:
    """Põe as colunas manuais de volta em cada NF. O nº do pedido digitado à
    mão só vale quando o DW não achou nenhum no texto da NF. O cliente final
    vem da aba Locais de entrega (na planilha é fórmula; aqui serve pra
    prévia do app). Cliente final digitado no layout anterior que não bate
    com a aba Locais vai pra Obs. Emissão, pra não se perder. Status de
    entrega da lista anterior vira a etapa equivalente da esteira. O que vem
    da aba Importar operacional só preenche o que está em branco."""
    locais = locais or {}
    importados = importados or {}
    resultado = selecionado.copy()
    for nome in COLUNAS_MANUAIS:
        resultado[nome] = ""
    for indice, linha in resultado.iterrows():
        anterior = manuais.get(chave_nf(linha["NF"], linha["Série"]))
        if anterior is None:
            resultado.at[indice, "Status entrega"] = status_entrega_inicial(linha)
            continue
        for nome in COLUNAS_MANUAIS:
            resultado.at[indice, nome] = anterior.get(nome, "")
        status = anterior.get("Status entrega", "")
        resultado.at[indice, "Status entrega"] = MIGRACAO_STATUS_ENTREGA.get(status, status)
        if not linha["Nº Pedido Vibra"] and anterior.get("Nº Pedido Vibra"):
            resultado.at[indice, "Nº Pedido Vibra"] = anterior["Nº Pedido Vibra"]
    for indice, linha in resultado.iterrows():
        importado = importados.get(chave_nf(linha["NF"], linha["Série"]), {})
        for nome, valor in importado.items():
            if not _str(linha[nome]):
                resultado.at[indice, nome] = valor
    resultado[COLUNA_CLIENTE_FINAL] = ""
    for indice, linha in resultado.iterrows():
        cliente = locais.get(chave_local(linha["Filial Vibra (CNPJ)"], linha["Local de entrega (nº)"]), "")
        resultado.at[indice, COLUNA_CLIENTE_FINAL] = cliente
        anterior = manuais.get(chave_nf(linha["NF"], linha["Série"])) or {}
        digitado = anterior.get(COLUNA_CLIENTE_FINAL_DIGITADO, "")
        if digitado and digitado != cliente:
            obs = _str(linha["Obs. Emissão"])
            resultado.at[indice, "Obs. Emissão"] = f"Cliente final: {digitado}" + (f" | {obs}" if obs else "")
    return resultado


# ============================================================================
# 4. Conteúdo das abas
# ============================================================================
def _texto(valor) -> str:
    """Texto que a planilha não deve converter (série "2", pedido com 10
    dígitos, CNPJ): o apóstrofo segura como texto no USER_ENTERED."""
    valor = "" if valor is None else str(valor)
    return f"'{valor}" if valor else ""


def _celula_data(valor) -> str:
    valor = _data(valor)
    return valor.strftime("%d/%m/%Y") if valor else ""


def formula_dias_em_atraso() -> str:
    a, d, k = _letra("NF"), _letra("Vencimento"), _letra("Status financeiro")
    p = PRIMEIRA_LINHA_DADOS
    return (
        f'={{"Dias em atraso";ARRAYFORMULA(IF(LEN({a}{p}:{a});'
        f'IF((({k}{p}:{k}="{STATUS_EM_ABERTO}")+({k}{p}:{k}="{STATUS_PAGO_PARCIAL}"))*({d}{p}:{d}<TODAY());'
        f'TODAY()-{d}{p}:{d};);))}}'
    )


def formula_cliente_final() -> str:
    """Procura filial (só os dígitos do CNPJ) + nº do local na aba Locais de
    entrega. NF sem local fica em branco; local que não está lá avisa.
    VLOOKUP + HSTACK porque MATCH não roda linha a linha no ARRAYFORMULA
    (devolve o primeiro resultado pra todas), e HSTACK evita o separador de
    colunas do {} que muda com a localidade da planilha."""
    a, h, n = _letra("NF"), _letra("Filial Vibra (CNPJ)"), _letra("Local de entrega (nº)")
    p = PRIMEIRA_LINHA_DADOS
    locais = f"'{ABA_LOCAIS}'"
    return (
        f'={{"{COLUNA_CLIENTE_FINAL}";ARRAYFORMULA(IF(LEN({a}{p}:{a})*LEN({n}{p}:{n});'
        f'IFERROR(VLOOKUP(REGEXREPLACE({h}{p}:{h}&"";"\\D";"")&"|"&{n}{p}:{n};'
        f'HSTACK(REGEXREPLACE({locais}!$A:$A&"";"\\D";"")&"|"&{locais}!$B:$B;{locais}!$C:$C);2;FALSE);'
        f'"{LOCAL_SEM_CADASTRO}");))}}'
    )


def formula_proxima_acao() -> str:
    a, k, o = _letra("NF"), _letra("Status financeiro"), _letra("Status entrega")
    p = PRIMEIRA_LINHA_DADOS
    return (
        f'={{"Próxima ação";ARRAYFORMULA(IF(LEN({a}{p}:{a});'
        f'IF(({k}{p}:{k}="{STATUS_EM_ABERTO}")+({k}{p}:{k}="{STATUS_PAGO_PARCIAL}");'
        f'IFERROR(VLOOKUP({o}{p}:{o};{ABA_LISTAS}!$A$2:$B$20;2;FALSE);"{ACAO_SEM_STATUS}");'
        f'IF({k}{p}:{k}="{STATUS_SEM_TITULO}";"{ACAO_SEM_TITULO}";"{ACAO_ENCERRADA}"));))}}'
    )


def valores_aba_controle(df: pd.DataFrame, atualizado_em: datetime) -> list:
    """Linhas 1 (grupos), 2 (cabeçalho com as fórmulas automáticas) e os dados,
    prontos pra gravar com value_input_option USER_ENTERED."""
    grupos = [""] * len(COLUNAS)
    # o extrator recria o filtro da aba sem critério: a lista volta pra "Todas"
    grupos[0] = OPCOES_FILTRO_RAPIDO[0]
    grupos[COLUNA_TITULO] = f"Do CIGAM (extrator, atualizado em {atualizado_em.strftime('%d/%m/%Y %H:%M')})"
    grupos[COLUNAS.index(COLUNAS_EMISSAO[0])] = "Emissão preenche"
    grupos[COLUNAS.index(COLUNAS_FINANCEIRO[0])] = "Financeiro preenche"
    grupos[COLUNAS.index(COLUNAS_FORMULA[0])] = "Automático"
    cabecalho = (COLUNAS_DW + [formula_cliente_final()] + COLUNAS_MANUAIS
                 + [formula_dias_em_atraso(), formula_proxima_acao()])

    valores = [grupos, cabecalho]
    for _, linha in df.iterrows():
        nf = re.sub(r"\D", "", str(linha["NF"]))
        saldo = linha["Saldo em aberto (R$)"]
        valores.append([
            int(nf) if nf else linha["NF"],
            _texto(linha["Série"]),
            _celula_data(linha["Emissão"]),
            _celula_data(linha["Vencimento"]),
            float(linha["Valor (R$)"]),
            "" if saldo is None or pd.isna(saldo) or saldo == 0 else float(saldo),
            _texto(linha["Nº Pedido Vibra"]),
            _texto(linha["Filial Vibra (CNPJ)"]),
            linha["Tipo de NF"],
            linha["Material"],
            linha["Status financeiro"],
            _celula_data(linha["Data pagamento / baixa"]),
            _texto(linha["NF de devolução"]),
            "" if linha["Local de entrega (nº)"] is None or pd.isna(linha["Local de entrega (nº)"])
            else int(linha["Local de entrega (nº)"]),
            _texto(linha["NF de remessa"]),
            "",
            *[linha[nome] for nome in COLUNAS_MANUAIS],
            "", "",
        ])
    if len(df):
        valores.append(linha_total(len(df)))
    return valores


# --- linha de totais, logo abaixo da última NF ---------------------------------
# SUBTOTAL só soma/conta as linhas que o filtro deixou visíveis, então os
# totais acompanham o filtro escolhido. "Em atraso" usa MAP pra aplicar o
# SUBTOTAL célula a célula (o truque SUMPRODUCT + OFFSET do Excel não
# funciona na planilha). A coluna A fica vazia: as fórmulas de O, T e U
# (ARRAYFORMULA da linha 2) só calculam onde A tem NF, e escrever nelas
# quebraria a fórmula. A linha fica fora do filtro da aba (que vai até a
# última NF), senão o próprio filtro a esconderia.
MARCA_LINHA_TOTAL = "TOTAL"
FORMATOS_LINHA_TOTAL = {
    "Emissão": '0" NF(s)"',
    "Valor (R$)": "[$R$ -416]#,##0.00",
    "Saldo em aberto (R$)": "[$R$ -416]#,##0.00",
    "Filial Vibra (CNPJ)": '0" NF(s) em atraso"',
    "Material": '"Em atraso: "[$R$ -416]#,##0.00',
}


def linha_total(qtd_nfs: int, separador: str = ";") -> list:
    """Linha de totais da aba de controle pra `qtd_nfs` NFs (linhas 3 em
    diante). O separador de argumentos é o da localidade da planilha (";"
    em pt_BR, gravando com USER_ENTERED)."""
    p, u = PRIMEIRA_LINHA_DADOS, PRIMEIRA_LINHA_DADOS + qtd_nfs - 1
    faixa = lambda nome: f"{_letra(nome)}{p}:{_letra(nome)}{u}"
    s = separador
    atraso = faixa("Dias em atraso")
    linha = [""] * len(COLUNAS)
    linha[COLUNAS.index("Série")] = MARCA_LINHA_TOTAL
    linha[COLUNAS.index("Emissão")] = f"=SUBTOTAL(103{s}{faixa('NF')})"
    linha[COLUNAS.index("Valor (R$)")] = f"=SUBTOTAL(109{s}{faixa('Valor (R$)')})"
    linha[COLUNAS.index("Saldo em aberto (R$)")] = f"=SUBTOTAL(109{s}{faixa('Saldo em aberto (R$)')})"
    linha[COLUNAS.index("Filial Vibra (CNPJ)")] = (
        f"=SUM(MAP({faixa('NF')}{s}{atraso}{s}LAMBDA(n{s}d{s}IF(d>0{s}SUBTOTAL(103{s}n){s}0))))")
    linha[COLUNAS.index("Material")] = (
        f"=SUM(MAP({faixa('Saldo em aberto (R$)')}{s}{atraso}{s}LAMBDA(v{s}d{s}IF(d>0{s}SUBTOTAL(109{s}v){s}0))))")
    return linha


def valores_aba_listas() -> list:
    linhas = [["Status entrega", "Próxima ação (se em aberto)", "", "Status financeiro", "O que significa"]]
    total = max(len(STATUS_ENTREGA_ACAO), len(SIGNIFICADO_STATUS))
    for i in range(total):
        entrega = STATUS_ENTREGA_ACAO[i] if i < len(STATUS_ENTREGA_ACAO) else ("", "")
        status = SIGNIFICADO_STATUS[i] if i < len(SIGNIFICADO_STATUS) else ("", "")
        linhas.append([entrega[0], entrega[1], "", status[0], status[1]])
    for _ in range(3):
        linhas.append([""] * 5)
    return linhas


def valores_aba_resumo() -> list:
    aba = f"'{ABA_CONTROLE}'"
    p = PRIMEIRA_LINHA_DADOS
    col = {nome: f"{aba}!${_letra(nome)}${p}:${_letra(nome)}" for nome in
           ("Valor (R$)", "Saldo em aberto (R$)", "Status financeiro", "Dias em atraso", "Próxima ação")}

    linhas = [["NFs Vibra: resumo por status", "", "", ""], ["", "", "", ""],
              ["Status financeiro", "Qtd NFs", "Valor (R$)", "Saldo em aberto (R$)"]]
    inicio_status = len(linhas) + 1
    for status, _ in SIGNIFICADO_STATUS:
        r = len(linhas) + 1
        linhas.append([
            status,
            f"=COUNTIFS({col['Status financeiro']};$A{r})",
            f"=SUMIFS({col['Valor (R$)']};{col['Status financeiro']};$A{r})",
            f"=SUMIFS({col['Saldo em aberto (R$)']};{col['Status financeiro']};$A{r})",
        ])
    fim_status = len(linhas)
    linha_total_status = len(linhas) + 1
    linhas.append(["Total", f"=SUM(B{inicio_status}:B{fim_status})", f"=SUM(C{inicio_status}:C{fim_status})",
                   f"=SUM(D{inicio_status}:D{fim_status})"])
    linhas.append(["", "", "", ""])
    linhas.append([
        "Em aberto e vencido",
        f"=COUNTIFS({col['Dias em atraso']};\">0\")",
        "",
        f"=SUMIFS({col['Saldo em aberto (R$)']};{col['Dias em atraso']};\">0\")",
    ])
    linhas.append(["", "", "", ""])
    linhas.append(["Em aberto por próxima ação", "Qtd NFs", "", "Saldo em aberto (R$)"])
    acoes = list(dict.fromkeys([acao for _, acao in STATUS_ENTREGA_ACAO] + [ACAO_SEM_STATUS, ACAO_SEM_TITULO]))
    inicio_acao = len(linhas) + 1
    for acao in acoes:
        r = len(linhas) + 1
        linhas.append([
            acao,
            f"=COUNTIFS({col['Próxima ação']};$A{r})",
            "",
            f"=SUMIFS({col['Saldo em aberto (R$)']};{col['Próxima ação']};$A{r})",
        ])
    fim_acao = len(linhas)
    linha_total_acao = len(linhas) + 1
    linhas.append(["Total em aberto", f"=SUM(B{inicio_acao}:B{fim_acao})", "", f"=SUM(D{inicio_acao}:D{fim_acao})"])
    linhas.append(["", "", "", ""])
    linhas.append([
        "Conferência: saldo por ação menos saldo por status (deve ser zero)", "", "",
        f"=D{linha_total_acao}-D{linha_total_status}",
    ])
    for _ in range(6):
        linhas.append(["", "", "", ""])
    return linhas


def valores_aba_fontes(atualizado_em: datetime, desde: date, qtd_linhas: int, solicitado_por: str) -> list:
    return [
        ["Gravado em", "Fonte", "Objeto", "Obtido via", "Consulta", "Parâmetros", "Executado em",
         "Solicitado por", "Linhas", "Alterações após a consulta", "Observações"],
        [
            f"'{ABA_CONTROLE}'!A2:{_letra(COLUNAS_DW[-1])}",
            "CIGAM (BigQuery)",
            ", ".join(TABELAS_CONSULTADAS),
            "Extrator controle_vibra.py (repositório Auto-Tracking-Top-Account-Management)",
            "SQL_NOTAS_VIBRA em controle_vibra.py: NFs de saída para a raiz de CNPJ da Vibra, com título, "
            "baixas (por codigoPartida), NF de devolução e itens.",
            f"raiz CNPJ {RAIZ_CNPJ_VIBRA}; NFs desde {desde.strftime('%d/%m/%Y')}, mais as em aberto "
            "e as que já estavam na planilha",
            f"'{atualizado_em.strftime('%d/%m/%Y %H:%M')}",
            solicitado_por,
            qtd_linhas,
            "Status financeiro, tipo de NF e nº do pedido calculados no extrator (regras em controle_vibra.py). "
            f"Colunas {_letra(COLUNAS_MANUAIS[0])} a {_letra(COLUNAS_MANUAIS[-1])} preservadas da planilha; "
            f"Cliente final (coluna {_letra(COLUNA_CLIENTE_FINAL)}) é fórmula pela aba {ABA_LOCAIS}.",
            f"As colunas A a {_letra(COLUNAS_DW[-1])} são reescritas a cada execução.",
        ],
        [""] * 11,
    ]


# ============================================================================
# 5. Formatação (batchUpdate), reaplicada a cada execução
# ============================================================================
VERDE = {"red": 0.2078, "green": 0.4078, "blue": 0.3294}
BRANCO = {"red": 1, "green": 1, "blue": 1}
PRETO = {"red": 0, "green": 0, "blue": 0}
CINZA_TEXTO = {"red": 0.502, "green": 0.502, "blue": 0.502}
AMARELO_CLARO = {"red": 1, "green": 0.949, "blue": 0.8}
AZUL_CLARO = {"red": 0.8667, "green": 0.9216, "blue": 0.9686}
CINZA_CLARO = {"red": 0.9294, "green": 0.9294, "blue": 0.9294}
VERMELHO_CLARO = {"red": 0.9569, "green": 0.8, "blue": 0.8}
VERMELHO = {"red": 0.7529, "green": 0, "blue": 0}
FAIXA = {"red": 0.9647, "green": 0.9725, "blue": 0.9765}

LARGURAS = {
    "NF": 70, "Série": 50, "Emissão": 90, "Vencimento": 90, "Valor (R$)": 110, "Saldo em aberto (R$)": 120,
    "Nº Pedido Vibra": 115, "Filial Vibra (CNPJ)": 150, "Tipo de NF": 140, "Material": 320,
    "Status financeiro": 120, "Data pagamento / baixa": 110, "NF de devolução": 95, "Local de entrega (nº)": 80,
    "NF de remessa": 95, "NF comodato Vibra": 120, "Situação NF assinada": 200,
    COLUNA_CLIENTE_FINAL: 250, "Status entrega": 170, "Data entrega / NF assinada": 120,
    "Obs. Emissão": 220, "Obs. financeiro": 300, "Dias em atraso": 80, "Próxima ação": 210,
}


def _faixa(sheet_id, linha_ini, linha_fim, col_ini, col_fim):
    faixa = {"sheetId": sheet_id, "startRowIndex": linha_ini, "startColumnIndex": col_ini, "endColumnIndex": col_fim}
    if linha_fim is not None:
        faixa["endRowIndex"] = linha_fim
    return faixa


def _formato(faixa, formato, campos):
    return {"repeatCell": {"range": faixa, "cell": {"userEnteredFormat": formato}, "fields": campos}}


def requests_formatacao_controle(sheet_id: int, total_linhas: int, regras_condicionais: int, bandas: list,
                                 linhas_dados: int = 0) -> list:
    """Requests de batchUpdate pra aba de controle. Limpa o que pode
    duplicar entre execuções (regras condicionais, faixas alternadas,
    mesclagens da linha 1, validações e notas antigas) e aplica o layout
    atual. `regras_condicionais` e `bandas` vêm dos metadados da aba.
    `linhas_dados` é a quantidade de NFs: o filtro e as faixas alternadas
    vão até a última NF, e a linha seguinte ganha o formato de totais."""
    n_col = len(COLUNAS)
    idx = {nome: i for i, nome in enumerate(COLUNAS)}
    dados = lambda c0, c1: _faixa(sheet_id, PRIMEIRA_LINHA_DADOS - 1, total_linhas, c0, c1)
    req = []

    for indice in reversed(range(regras_condicionais)):
        req.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": indice}})
    for banda_id in bandas:
        req.append({"deleteBanding": {"bandedRangeId": banda_id}})
    req.append({"unmergeCells": {"range": _faixa(sheet_id, 0, 1, 0, n_col)}})
    req.append({"setDataValidation": {"range": _faixa(sheet_id, PRIMEIRA_LINHA_DADOS - 1, total_linhas, 0, n_col)}})
    req.append({"updateCells": {"range": _faixa(sheet_id, 0, 2, 0, n_col), "fields": "note"}})
    req.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 0}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})

    # zera toda a formatação da área (inclusive a do layout anterior) antes
    # de aplicar a atual
    req.append(_formato(_faixa(sheet_id, 0, total_linhas, 0, n_col),
                        {"textFormat": {"fontFamily": "Arial", "fontSize": 10}, "verticalAlignment": "MIDDLE"},
                        "userEnteredFormat"))

    grupos = [
        (0, COLUNAS_FILTRO_RAPIDO, BRANCO, VERDE),
        (COLUNA_PERIODO_DE, COLUNA_PERIODO_DE + 1, BRANCO, VERDE),
        (COLUNA_PERIODO_ATE, COLUNA_PERIODO_ATE + 1, BRANCO, VERDE),
        (COLUNA_TITULO, idx[COLUNA_CLIENTE_FINAL] + 1, VERDE, BRANCO),
        (idx[COLUNAS_EMISSAO[0]], idx[COLUNAS_EMISSAO[-1]] + 1, AMARELO_CLARO, PRETO),
        (idx[COLUNAS_FINANCEIRO[0]], idx[COLUNAS_FINANCEIRO[-1]] + 1, AZUL_CLARO, PRETO),
        (idx[COLUNAS_FORMULA[0]], idx[COLUNAS_FORMULA[-1]] + 1, CINZA_CLARO, PRETO),
    ]
    for c0, c1, fundo, texto in grupos:
        if c1 - c0 > 1:
            req.append({"mergeCells": {"range": _faixa(sheet_id, 0, 1, c0, c1), "mergeType": "MERGE_ALL"}})
        req.append(_formato(_faixa(sheet_id, 0, 1, c0, c1),
                            {"backgroundColor": fundo, "horizontalAlignment": "CENTER",
                             "textFormat": {"bold": True, "foregroundColor": texto, "fontFamily": "Arial"}},
                            "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,"
                            "userEnteredFormat.horizontalAlignment"))

    # filtro rápido: lista em A1:D1 e datas do período em E1 / F1
    req.append({"setDataValidation": {"range": _faixa(sheet_id, 0, 1, 0, COLUNAS_FILTRO_RAPIDO), "rule": {
        "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in OPCOES_FILTRO_RAPIDO]},
        "strict": True, "showCustomUi": True}}})
    for coluna, rotulo in ((COLUNA_PERIODO_DE, "de"), (COLUNA_PERIODO_ATE, "até")):
        req.append({"setDataValidation": {"range": _faixa(sheet_id, 0, 1, coluna, coluna + 1), "rule": {
            "condition": {"type": "DATE_IS_VALID"}, "strict": True,
            "inputMessage": "Período do filtro Pagos no período (dd/mm/aaaa)"}}})
        req.append(_formato(_faixa(sheet_id, 0, 1, coluna, coluna + 1),
                            {"numberFormat": {"type": "DATE", "pattern": f'"{rotulo} "dd/mm/yyyy'}},
                            "userEnteredFormat.numberFormat"))
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 30}, "fields": "pixelSize"}})

    # filtro e faixas alternadas até a última NF; a linha de totais fica fora
    fim_dados = PRIMEIRA_LINHA_DADOS - 1 + linhas_dados if linhas_dados else total_linhas
    req.append({"addBanding": {"bandedRange": {
        "range": _faixa(sheet_id, LINHA_CABECALHO - 1, fim_dados, 0, n_col),
        "rowProperties": {"headerColor": VERDE, "firstBandColor": BRANCO, "secondBandColor": FAIXA}}}})
    req.append(_formato(_faixa(sheet_id, LINHA_CABECALHO - 1, LINHA_CABECALHO, 0, n_col),
                        {"textFormat": {"bold": True, "foregroundColor": BRANCO, "fontFamily": "Arial"},
                         "horizontalAlignment": "CENTER", "wrapStrategy": "WRAP"},
                        "userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment,"
                        "userEnteredFormat.wrapStrategy"))

    moeda = {"numberFormat": {"type": "CURRENCY", "pattern": "[$R$ -416]#,##0.00"}}
    data = {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yyyy"}, "horizontalAlignment": "CENTER"}
    centro = {"horizontalAlignment": "CENTER", "numberFormat": {"type": "NUMBER", "pattern": "0"}}
    for nome in ("Valor (R$)", "Saldo em aberto (R$)"):
        req.append(_formato(dados(idx[nome], idx[nome] + 1), moeda, "userEnteredFormat.numberFormat"))
    for nome in ("Emissão", "Vencimento", "Data pagamento / baixa", "Data entrega / NF assinada"):
        req.append(_formato(dados(idx[nome], idx[nome] + 1), data,
                            "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"))
    for nome in ("NF", "Dias em atraso"):
        req.append(_formato(dados(idx[nome], idx[nome] + 1), centro,
                            "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"))
    req.append(_formato(dados(idx["Série"], idx["Série"] + 1), {"horizontalAlignment": "CENTER"},
                        "userEnteredFormat.horizontalAlignment"))
    for nome in ("Material", COLUNA_CLIENTE_FINAL, "Situação NF assinada", "Obs. Emissão", "Obs. financeiro"):
        req.append(_formato(dados(idx[nome], idx[nome] + 1), {"wrapStrategy": "WRAP"},
                            "userEnteredFormat.wrapStrategy"))

    # a visão "Atualizar o controle da Vibra" oculta colunas: o filtro volta
    # pra "Todas as NFs", então as colunas voltam todas
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": n_col},
        "properties": {"hiddenByUser": False}, "fields": "hiddenByUser"}})
    for nome, largura in LARGURAS.items():
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": idx[nome], "endIndex": idx[nome] + 1},
            "properties": {"pixelSize": largura}, "fields": "pixelSize"}})
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
        "properties": {"pixelSize": 40}, "fields": "pixelSize"}})

    req.append({"setDataValidation": {
        "range": dados(idx["Status entrega"], idx["Status entrega"] + 1),
        "rule": {"condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": f"={ABA_LISTAS}!$A$2:$A$20"}]},
                 "strict": True, "showCustomUi": True}}})
    req.append({"setDataValidation": {
        "range": dados(idx["Data entrega / NF assinada"], idx["Data entrega / NF assinada"] + 1),
        "rule": {"condition": {"type": "DATE_IS_VALID"}, "strict": True,
                 "inputMessage": "Informe uma data (dd/mm/aaaa)"}}})

    # NF encerrada fica em cinza. A regra olha a "Próxima ação" (fórmula da
    # coluna T) em vez de combinar os status com AND(...;...): o separador
    # de argumentos depende da localidade da planilha, e esta não usa nenhum
    letra_acao = _letra("Próxima ação")
    regras = [
        ([dados(0, n_col)], {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue":
            f'=${letra_acao}{PRIMEIRA_LINHA_DADOS}="{ACAO_ENCERRADA}"'}]},
         {"textFormat": {"foregroundColor": CINZA_TEXTO}}),
        ([dados(idx["Próxima ação"], idx["Próxima ação"] + 1)],
         {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": "Emissão"}]}, {"backgroundColor": AMARELO_CLARO}),
        ([dados(idx["Próxima ação"], idx["Próxima ação"] + 1)],
         {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": "Financeiro"}]}, {"backgroundColor": AZUL_CLARO}),
        ([dados(idx["Próxima ação"], idx["Próxima ação"] + 1)],
         {"type": "TEXT_EQ", "values": [{"userEnteredValue": ACAO_SEM_STATUS}]}, {"backgroundColor": VERMELHO_CLARO}),
        ([dados(idx["Dias em atraso"], idx["Dias em atraso"] + 1)],
         {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
         {"textFormat": {"foregroundColor": VERMELHO, "bold": True}}),
    ]
    for posicao, (faixas, condicao, formato) in enumerate(regras):
        req.append({"addConditionalFormatRule": {"index": posicao, "rule": {
            "ranges": faixas, "booleanRule": {"condition": condicao, "format": formato}}}})

    req.append({"setBasicFilter": {"filter": {"range": _faixa(sheet_id, LINHA_CABECALHO - 1, fim_dados, 0, n_col)}}})
    if linhas_dados:
        req += requests_linha_total(sheet_id, fim_dados)
    req.append({"updateCells": {
        "rows": [{"values": [{"note":
            f"Colunas A a {_letra(COLUNAS_DW[-1])} vêm do CIGAM pelo extrator (controle_vibra.py) e são "
            "reescritas a cada atualização: "
            "não edite. Detalhes da consulta na aba Fontes."}]}],
        "fields": "note", "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": 0}}})
    req.append({"updateCells": {
        "rows": [{"values": [{"note":
            f"Fórmula: procura a filial (coluna {_letra('Filial Vibra (CNPJ)')}) e o nº do local "
            f"(coluna {_letra('Local de entrega (nº)')}) na aba {ABA_LOCAIS}. "
            "Para corrigir ou cadastrar um cliente final, edite lá."}]}],
        "fields": "note", "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": idx[COLUNA_CLIENTE_FINAL]}}})
    req.append({"updateCells": {
        "rows": [{"values": [{"note":
            "Filtro rápido: escolha uma opção e a lista é filtrada na hora (vale pra todo mundo com a "
            "planilha aberta). Para Pagos no período, informe as datas nas duas células ao lado. "
            "Volte para Todas as NFs ao terminar."}]}],
        "fields": "note", "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0}}})
    req.append({"updateCells": {
        "rows": [{"values": [{"note": "Opções vêm da aba Listas. Para incluir uma nova, acrescente lá."}]}],
        "fields": "note", "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": idx["Status entrega"]}}})
    return req


def requests_linha_total(sheet_id: int, indice: int) -> list:
    """Formato da linha de totais (índice da linha a partir de 0)."""
    n_col = len(COLUNAS)
    linha = _faixa(sheet_id, indice, indice + 1, 0, n_col)
    req = [
        {"setDataValidation": {"range": linha}},
        _formato(linha, {"backgroundColor": AMARELO_CLARO, "textFormat": {"bold": True, "fontFamily": "Arial"},
                         "verticalAlignment": "MIDDLE"},
                 "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.verticalAlignment"),
        {"updateBorders": {"range": linha, "top": {"style": "SOLID_MEDIUM", "color": VERDE}}},
    ]
    for nome, padrao in FORMATOS_LINHA_TOTAL.items():
        c = COLUNAS.index(nome)
        req.append(_formato(_faixa(sheet_id, indice, indice + 1, c, c + 1),
                            {"numberFormat": {"type": "NUMBER", "pattern": padrao}, "horizontalAlignment": "RIGHT"},
                            "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"))
    return req


def requests_formatacao_auxiliares(id_resumo: int, id_listas: int, id_fontes: int, linhas_resumo: int) -> list:
    req = []
    moeda = {"numberFormat": {"type": "CURRENCY", "pattern": "[$R$ -416]#,##0.00"}}
    req.append(_formato(_faixa(id_resumo, 0, linhas_resumo, 0, 6), {"textFormat": {"fontFamily": "Arial", "fontSize": 10}},
                        "userEnteredFormat"))
    req.append(_formato(_faixa(id_resumo, 0, 1, 0, 1), {"textFormat": {"bold": True, "fontSize": 14, "fontFamily": "Arial"}},
                        "userEnteredFormat.textFormat"))
    req.append(_formato(_faixa(id_resumo, 2, linhas_resumo, 2, 4), moeda, "userEnteredFormat.numberFormat"))
    resumo = valores_aba_resumo()
    for i, linha in enumerate(resumo):
        rotulo = linha[0]
        if rotulo in ("Status financeiro", "Em aberto por próxima ação"):
            req.append(_formato(_faixa(id_resumo, i, i + 1, 0, 4),
                                {"backgroundColor": VERDE, "textFormat": {"bold": True, "foregroundColor": BRANCO,
                                                                          "fontFamily": "Arial"}},
                                "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"))
        elif rotulo in ("Total", "Total em aberto", "Em aberto e vencido"):
            req.append(_formato(_faixa(id_resumo, i, i + 1, 0, 4), {"textFormat": {"bold": True, "fontFamily": "Arial"}},
                                "userEnteredFormat.textFormat"))
        elif rotulo.startswith("Conferência"):
            req.append(_formato(_faixa(id_resumo, i, i + 1, 0, 4),
                                {"textFormat": {"italic": True, "foregroundColor": CINZA_TEXTO, "fontFamily": "Arial"},
                                 "wrapStrategy": "WRAP"},
                                "userEnteredFormat.textFormat,userEnteredFormat.wrapStrategy"))
    for i, largura in enumerate([330, 90, 140, 150]):
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": id_resumo, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": largura}, "fields": "pixelSize"}})

    req.append(_formato(_faixa(id_listas, 0, 30, 0, 6), {"textFormat": {"fontFamily": "Arial", "fontSize": 10}},
                        "userEnteredFormat"))
    req.append(_formato(_faixa(id_listas, 0, 1, 0, 5),
                        {"backgroundColor": VERDE, "textFormat": {"bold": True, "foregroundColor": BRANCO}},
                        "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"))
    req.append(_formato(_faixa(id_listas, 0, 1, 2, 3), {"backgroundColor": BRANCO}, "userEnteredFormat.backgroundColor"))
    req.append(_formato(_faixa(id_listas, 1, 20, 4, 5), {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy"))
    for i, largura in enumerate([190, 230, 30, 140, 420]):
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": id_listas, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": largura}, "fields": "pixelSize"}})

    req.append(_formato(_faixa(id_fontes, 0, 20, 0, 11), {"textFormat": {"fontFamily": "Arial", "fontSize": 10}},
                        "userEnteredFormat"))
    req.append(_formato(_faixa(id_fontes, 0, 1, 0, 11),
                        {"backgroundColor": VERDE, "textFormat": {"bold": True, "foregroundColor": BRANCO}},
                        "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"))
    req.append(_formato(_faixa(id_fontes, 0, 5, 0, 11), {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
                        "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"))
    return req


# ============================================================================
# 6. Execução
# ============================================================================
def agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


def _aba_se_existir(planilha, titulo: str):
    import gspread

    try:
        return planilha.worksheet(titulo)
    except gspread.WorksheetNotFound:
        return None


def _obter_ou_criar_aba(planilha, titulo: str, linhas: int, colunas: int):
    import gspread

    try:
        aba = planilha.worksheet(titulo)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=titulo, rows=linhas, cols=colunas)
    if aba.row_count < linhas or aba.col_count < colunas:
        aba.resize(rows=max(aba.row_count, linhas), cols=max(aba.col_count, colunas))
    return aba


def preparar_controle(client_bq, valores_aba_atual: list, desde: date = DESDE_PADRAO,
                      valores_locais: list = None, valores_importacao: list = None) -> pd.DataFrame:
    """Consulta o DW, aplica as regras e devolve a tabela final (DW +
    colunas manuais), sem gravar nada. Usado pela prévia do app e pela
    sincronização."""
    manuais = ler_preenchimento_manual(valores_aba_atual)
    controle = montar_controle(buscar_notas_vibra(client_bq))
    return mesclar_manuais(selecionar_linhas(controle, manuais, desde), manuais,
                           ler_locais_entrega(valores_locais or []), ler_importacao(valores_importacao or []))


def sincronizar_planilha(client_bq, gc, planilha_id: str = PLANILHA_ID_PADRAO, desde: date = DESDE_PADRAO,
                         solicitado_por: str = "") -> dict:
    """Atualiza a planilha inteira: lê o que o time preencheu, consulta o
    DW, reescreve as abas Controle, Resumo, Listas e Fontes, acrescenta na
    aba Locais de entrega os locais que ainda não estão lá e reaplica a
    formatação. Devolve um resumo com a tabela gravada."""
    planilha = gc.open_by_key(planilha_id)
    aba = _obter_ou_criar_aba(planilha, ABA_CONTROLE, 500, len(COLUNAS))
    locais_aba = _obter_ou_criar_aba(planilha, ABA_LOCAIS, 100, len(CABECALHO_LOCAIS))
    valores_locais = locais_aba.get_all_values()
    if not ler_locais_entrega(valores_locais) and not any(any(c for c in l) for l in valores_locais):
        locais_aba.update(values=[CABECALHO_LOCAIS], range_name="A1", value_input_option="USER_ENTERED")
        valores_locais = [CABECALHO_LOCAIS]
    importacao_aba = _aba_se_existir(planilha, ABA_IMPORTACAO)
    valores_importacao = importacao_aba.get_all_values() if importacao_aba else []
    tabela = preparar_controle(client_bq, aba.get_all_values(), desde, valores_locais, valores_importacao)
    atualizado_em = agora()

    valores = valores_aba_controle(tabela, atualizado_em)
    total_linhas = max(500, len(valores) + 100)
    if aba.row_count < total_linhas or aba.col_count < len(COLUNAS):
        aba.resize(rows=max(aba.row_count, total_linhas), cols=max(aba.col_count, len(COLUNAS)))
    linhas_resumo = valores_aba_resumo()
    listas = _obter_ou_criar_aba(planilha, ABA_LISTAS, 30, 6)
    resumo = _obter_ou_criar_aba(planilha, ABA_RESUMO, max(40, len(linhas_resumo)), 6)
    fontes = _obter_ou_criar_aba(planilha, ABA_FONTES, 20, 11)

    # formatação ANTES dos valores: um valor gravado numa célula que ainda
    # está dentro de uma mesclagem do layout anterior se perde quando a
    # mesclagem é desfeita (foi o que aconteceu com "Emissão preenche" na
    # migração do layout manual)
    metadados = planilha.fetch_sheet_metadata(params={
        "fields": "sheets.properties.sheetId,sheets.conditionalFormats,sheets.bandedRanges.bandedRangeId"})
    da_aba = next(s for s in metadados["sheets"] if s["properties"]["sheetId"] == aba.id)
    requests = requests_formatacao_controle(
        aba.id, aba.row_count, len(da_aba.get("conditionalFormats", [])),
        [b["bandedRangeId"] for b in da_aba.get("bandedRanges", [])], linhas_dados=len(tabela))
    requests += requests_formatacao_auxiliares(resumo.id, listas.id, fontes.id, resumo.row_count)
    planilha.batch_update({"requests": requests})

    aba.batch_clear([f"A1:{_letra(COLUNAS[-1])}{aba.row_count}"])
    aba.update(values=valores, range_name="A1", value_input_option="USER_ENTERED")
    listas.batch_clear([f"A1:F{listas.row_count}"])
    listas.update(values=valores_aba_listas(), range_name="A1", value_input_option="USER_ENTERED")
    resumo.batch_clear([f"A1:F{resumo.row_count}"])
    resumo.update(values=linhas_resumo, range_name="A1", value_input_option="USER_ENTERED")
    fontes.batch_clear([f"A1:K{fontes.row_count}"])
    fontes.update(values=valores_aba_fontes(atualizado_em, desde, len(tabela), solicitado_por),
                  range_name="A1", value_input_option="USER_ENTERED")
    novos_locais = locais_sem_cadastro(tabela, ler_locais_entrega(valores_locais))
    if novos_locais:
        locais_aba.append_rows(novos_locais, value_input_option="USER_ENTERED")
    if ler_importacao(valores_importacao):  # usada: limpa os dados, fica o cabeçalho
        importacao_aba.batch_clear([f"A2:Z{max(importacao_aba.row_count, 2)}"])

    return {
        "linhas": len(tabela),
        "locais_novos": len(novos_locais),
        "atualizado_em": atualizado_em,
        "url": f"https://docs.google.com/spreadsheets/d/{planilha_id}/edit#gid={aba.id}",
        "tabela": tabela,
    }


def _clientes_pela_credencial(info_credencial: dict):
    import gspread
    from google.cloud import bigquery
    from google.oauth2 import service_account

    credenciais = service_account.Credentials.from_service_account_info(
        info_credencial,
        scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/spreadsheets"],
    )
    return bigquery.Client(project=PROJECT_ID, credentials=credenciais), gspread.authorize(credenciais)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Atualiza o controle de NFs da Vibra a partir do DW.")
    parser.add_argument("--credenciais", required=True, help="JSON da service account")
    parser.add_argument("--planilha", default=PLANILHA_ID_PADRAO, help="ID da planilha de controle")
    parser.add_argument("--desde", default=DESDE_PADRAO.isoformat(), help="emissão mínima (AAAA-MM-DD)")
    parser.add_argument("--somente-ler", action="store_true", help="mostra a tabela sem gravar na planilha")
    args = parser.parse_args(argv)

    with open(args.credenciais, encoding="utf-8") as arquivo:
        client_bq, gc = _clientes_pela_credencial(json.load(arquivo))
    desde = date.fromisoformat(args.desde)

    if args.somente_ler:
        aba = gc.open_by_key(args.planilha).worksheet(ABA_CONTROLE)
        tabela = preparar_controle(client_bq, aba.get_all_values(), desde)
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(tabela[["NF", "Série", "Vencimento", "Valor (R$)", "Status financeiro", "Status entrega"]])
        return 0

    resultado = sincronizar_planilha(client_bq, gc, args.planilha, desde, solicitado_por="agendamento")
    print(f"{resultado['linhas']} NFs gravadas em {resultado['atualizado_em']:%d/%m/%Y %H:%M}: {resultado['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
