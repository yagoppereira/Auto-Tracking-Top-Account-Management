"""O Apps Script da planilha (apps_script/ControleVibra.gs) é o espelho de
controle_vibra.py. Estes testes rodam as regras do .gs no Node e exigem o
mesmo SQL e as mesmas células gravadas que a versão Python."""
import json
import re
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import controle_vibra as cv
from test_controle_vibra import aba_locais, nf_dw, planilha_layout_antigo

HARNESS = Path(__file__).parent / "harness_apps_script.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node não instalado")


def _rodar_node(entrada: dict) -> dict:
    saida = subprocess.run(["node", str(HARNESS)], input=json.dumps(entrada), capture_output=True,
                           text=True, check=True)
    return json.loads(saida.stdout)


def _como_bigquery(linha: dict) -> dict:
    """O que o BigQuery.Jobs.query do Apps Script devolve: tudo texto,
    NULL como null."""
    convertida = {}
    for chave, valor in linha.items():
        if valor is None or valor is pd.NA:
            convertida[chave] = None
        elif isinstance(valor, bool):
            convertida[chave] = "true" if valor else "false"
        elif isinstance(valor, date):
            convertida[chave] = valor.isoformat()
        else:
            convertida[chave] = str(valor)
    return convertida


def test_sql_e_o_mesmo_nas_duas_versoes():
    sql_js = _rodar_node({"somente_sql": True})["sql"]
    assert sql_js == cv.SQL_NOTAS_VIBRA


CASOS = [
    nf_dw(textos="OC: 4503762321", local_entrega=None),                              # aberta, já na planilha
    nf_dw(nf="24056", cnpj="34274233008006", local_entrega=1, emissao=date(2026, 6, 15)),  # local cadastrado
    nf_dw(nf="24865", cnpj="34274233002571", local_entrega=9, emissao=date(2026, 7, 23)),  # local sem cliente
    nf_dw(nf="26133", cnpj="34274233000102", local_entrega=12, emissao=date(2026, 10, 8)),  # local novo
    nf_dw(nf="23224", vencimento=date(2026, 5, 17), tem_cta=False),                  # aberta, venda de sonda
    nf_dw(nf="12121", serie="S1", emissao=date(2026, 2, 20), valor_titulo=Decimal("7851.02"),
          total_nf=Decimal("7851.02"), pago=Decimal("7851.02"), data_baixa=date(2026, 2, 27),
          tem_licenciamento=True, tem_cta=pd.NA, tem_sonda=pd.NA,
          textos="NF - FEV_26 (Contrato CTA SMART no 4600213963)"),                 # paga, mensalidade
    nf_dw(nf="22114", emissao=date(2026, 1, 28), valor_titulo=Decimal("1326.48"),
          total_nf=Decimal("1326.48"), abatido=Decimal("1326.48"), data_baixa=date(2026, 6, 3),
          nfs_devolucao="23920", data_devolucao=date(2026, 6, 3), tem_cta=False, tem_sonda=False),
    nf_dw(nf="30001", emissao=date(2026, 3, 1), valor_titulo=Decimal("700"), total_nf=Decimal("700"),
          abatido=Decimal("700"), data_baixa=date(2026, 4, 30), serie="S", tem_cta=None, tem_sonda=None),
    nf_dw(nf="30002", emissao=date(2026, 3, 2), valor_titulo=Decimal("520"), total_nf=Decimal("520"),
          pago=Decimal("50"), data_baixa=date(2026, 3, 9)),                            # parcial
    nf_dw(nf="30003", emissao=date(2026, 3, 3), valor_titulo=Decimal("100"), total_nf=Decimal("100"),
          abatido=Decimal("40"), perda=Decimal("60"), data_baixa=date(2026, 9, 1)),    # perda
    nf_dw(nf="30004", emissao=date(2026, 3, 4), status="5", data_cancelamento="05/03/2026",
          total_nf=None),                                                              # cancelada
    nf_dw(nf="30005", emissao=date(2026, 10, 15), valor_titulo=None, pago=None, abatido=None,
          perda=None, vencimento=None, material=None),                                 # sem título
    nf_dw(nf="17000", emissao=date(2024, 5, 1), vencimento=date(2024, 6, 1)),          # aberta e antiga
    nf_dw(nf="20241", emissao=date(2025, 8, 14), pago=Decimal("35861.37"),
          data_baixa=date(2025, 12, 12)),                                              # paga e antiga: sai
    nf_dw(nf="21903", serie="S1", emissao=date(2026, 5, 26), vencimento=date(2026, 6, 25),
          valor_titulo=Decimal("4561.68"), total_nf=Decimal("4561.68"), tem_licenciamento=True),
    nf_dw(nf="25441", emissao=date(2026, 8, 21), vencimento=date(2026, 9, 20), tem_cta=False,
          textos="OC 4503906558 e OC 4504019219 e de novo 4503906558"),
    nf_dw(nf="25263", emissao=date(2026, 8, 13), vencimento=date(2026, 9, 12), cnpj="34274233008006",
          local_entrega=3, nf_remessa="25265", cliente_remessa="006456",
          nome_remessa="CIA. DE FERRO LIGAS DA BAHIA - FERBASA", municipio_remessa="POJUCA",
          uf_remessa="BA"),                                                            # triangular
]


def _python(casos, aba, desde, locais=()):
    manuais = cv.ler_preenchimento_manual(aba)
    tabela = cv.mesclar_manuais(cv.selecionar_linhas(cv.montar_controle(pd.DataFrame(casos)), manuais, desde),
                                manuais, cv.ler_locais_entrega(list(locais)))
    largura = len(cv.COLUNAS_DW) + 1 + len(cv.COLUNAS_MANUAIS)  # A a S (O = cliente final, fórmula)
    linhas = [linha[:largura] for linha in cv.valores_aba_controle(tabela, datetime(2026, 10, 19))[2:2 + len(tabela)]]
    return linhas, cv.locais_sem_cadastro(tabela, cv.ler_locais_entrega(list(locais)))


def _js(casos, aba, desde, locais=()):
    saida = _rodar_node({"linhas": [_como_bigquery(c) for c in casos], "aba": aba, "desde": desde,
                         "locais": list(locais)})
    return saida["linhas"], saida["novos"]


def test_linha_de_totais_igual_nas_duas_versoes():
    saida = _rodar_node({"linhas": [_como_bigquery(c) for c in CASOS], "aba": [], "desde": "2026-01-01",
                         "locais": []})
    qtd = len(saida["linhas"])
    virgula, ponto_e_virgula = saida["total"]
    assert ponto_e_virgula == cv.linha_total(qtd, ";")
    assert virgula == cv.linha_total(qtd, ",")
    # a tabela gravada pelo Python termina com a linha de totais
    manuais = cv.ler_preenchimento_manual([])
    tabela = cv.mesclar_manuais(cv.selecionar_linhas(cv.montar_controle(pd.DataFrame(CASOS)), manuais,
                                                     date(2026, 1, 1)), manuais)
    assert cv.valores_aba_controle(tabela, datetime(2026, 10, 19))[-1] == cv.linha_total(qtd)


def test_mesmas_celulas_nas_duas_versoes():
    aba = planilha_layout_antigo()
    esperado, novos_py = _python(CASOS, aba, date(2026, 1, 1), aba_locais())
    obtido, novos_js = _js(CASOS, aba, "2026-01-01", aba_locais())
    assert [l[0] for l in obtido] == [l[0] for l in esperado]
    for linha_js, linha_py in zip(obtido, esperado):
        assert linha_js == linha_py, linha_py[0]
    assert novos_js == novos_py and novos_py


def test_mesmas_celulas_sem_planilha_anterior():
    assert _js(CASOS, [], "2026-01-01") == tuple(map(list, _python(CASOS, [], date(2026, 1, 1))))


# ----------------------------------------------------------------------------
# Painel de filtros (apps_script/FiltrosVibra.gs)
# ----------------------------------------------------------------------------
def _linha_planilha(nf, status, entrega="", pagamento=None, atraso="", valor=100.0, saldo=""):
    """Linha como o getValues devolve da aba de controle (A a U)."""
    linha = [""] * len(cv.COLUNAS)
    linha[cv.COLUNAS.index("NF")] = nf
    linha[cv.COLUNAS.index("Status financeiro")] = status
    linha[cv.COLUNAS.index("Status entrega")] = entrega
    linha[cv.COLUNAS.index("Data pagamento / baixa")] = {"data": pagamento} if pagamento else ""
    linha[cv.COLUNAS.index("Dias em atraso")] = atraso
    linha[cv.COLUNAS.index("Valor (R$)")] = valor
    linha[cv.COLUNAS.index("Saldo em aberto (R$)")] = saldo
    return linha


def _filtros():
    linhas = [
        _linha_planilha(1, "Em aberto", "Comodato assinado", atraso=12, saldo=100.0),
        _linha_planilha(2, "Pago parcial", "Não se aplica", valor=200.0, saldo=150.0),
        _linha_planilha(3, "Em aberto", "A validar", atraso=40, saldo=100.0),
        _linha_planilha(4, "Sem título", ""),
        _linha_planilha(5, "Pago", "Comodato assinado", pagamento="2026-09-08"),
        _linha_planilha(6, "Pago", "Não instalado", pagamento="2026-09-30"),  # paga, falta instalar
        _linha_planilha(7, "Pago", "", pagamento="2026-10-01"),
        _linha_planilha(8, "Devolvida", "A validar", pagamento="2026-09-10"),
        _linha_planilha(9, "Cancelada", ""),
        _linha_planilha(10, "em aberto", "não se aplica", saldo=100.0),  # planilha ignora maiúsculas
    ]
    colunas = {nome: i for i, nome in enumerate(cv.COLUNAS)}
    return _rodar_node({"filtros": {"colunas": colunas, "linhas": linhas, "de": "2026-09-01",
                                    "ate": "2026-09-30"}})["filtros"]


def test_filtros_contam_as_nfs_de_cada_condicao():
    saida = _filtros()
    contagem = {opcao: r["resumo"]["nfs"] for opcao, r in saida.items()}
    assert contagem == {
        "controle_vibra": 3,            # em aberto/parcial e entregues (ou não se aplica): 1, 2 e 10
        "pendentes_pagamento": 4,       # 1, 2, 3 e 10: todo título em aberto ou pago em parte
        "pendentes_entregues": 3,       # 1, 2 e 10: com entrega concluída ou não se aplica
        "pendentes_entregues_atraso": 1,  # só a 1 tem dias em atraso
        "aguardando_emissao": 1,        # só a 3 (a validar); a 6 já foi paga, a 8 é devolvida e a 1 está ok para cobrança
        "pagos": 3,
        "pagos_periodo": 2,             # 08/09 e 30/09 (o fim do período entra); 01/10 fica fora
        "encerradas_sem_pagamento": 2,  # devolvida e cancelada
    }
    k, p = cv._letra("Status financeiro"), cv._letra("Status entrega")
    # sai a NF já paga e a que está ok para cobrança ("Não instalado, comodato assinado")
    assert saida["aguardando_emissao"]["formula"] == (
        f'=(((${p}3="A validar")+(${p}3="Não entregue")+(${p}3="Não instalado")'
        f'+(${p}3="Aguardando comodato da Vibra")+(${p}3="Aguardando cliente assinar comodato")'
        f'+(${p}3="Problema na entrega")+0>0)*((${k}3="Em aberto")+(${k}3="Pago parcial")+0>0)*1)>0')
    assert saida["pendentes_pagamento"]["resumo"] == {"nfs": 4, "valor": 500, "saldo": 450}
    assert saida["pendentes_entregues"]["resumo"] == {"nfs": 3, "valor": 400, "saldo": 350}


def test_formula_do_filtro_usa_as_colunas_do_layout_e_nao_depende_da_localidade():
    saida = _filtros()
    k, l, p = cv._letra("Status financeiro"), cv._letra("Data pagamento / baixa"), cv._letra("Status entrega")
    # 46266 = 01/09/2026 e 46295 = 30/09/2026 em nº de série de data da planilha
    assert saida["pagos_periodo"]["formula"] == (
        f'=(((${k}3="Pago")+0>0)*((${l}3>=46266)*(${l}3<=46295)>0)*1)>0')
    # lógico sozinho comparado com número: FALSO>0 é VERDADEIRO na planilha
    # (lógico é maior que número) e passava tudo. O +0 e o *1 viram 1/0.
    assert saida["pagos"]["formula"] == f'=(((${k}3="Pago")+0>0)*1)>0'
    assert saida["pendentes_entregues"]["formula"] == (
        f'=(((${k}3="Em aberto")+(${k}3="Pago parcial")+0>0)'
        f'*((${p}3="Não instalado")+(${p}3="Não instalado, comodato assinado")'
        f'+(${p}3="Aguardando comodato da Vibra")+(${p}3="Aguardando cliente assinar comodato")'
        f'+(${p}3="Comodato assinado")+(${p}3="Não se aplica")+0>0)*1)>0')
    for resultado in saida.values():
        assert resultado["formula"].endswith("*1)>0")
    for resultado in saida.values():  # sem função de vários argumentos: nada de ";" nem ","
        fora_de_texto = re.sub(r'"[^"]*"', '""', resultado["formula"])  # vírgula em status é texto
        assert ";" not in fora_de_texto and "," not in fora_de_texto


def test_filtro_rapido_da_linha_1_tem_as_mesmas_opcoes_nas_duas_versoes():
    colunas = {nome: i for i, nome in enumerate(cv.COLUNAS)}
    saida = _rodar_node({"filtros": {"colunas": colunas, "linhas": [], "de": "2026-09-01", "ate": "2026-09-30"}})
    # a validação da lista em A1 vem do Python; o onEdit do .gs procura pelo rótulo
    assert saida["opcoes"] == cv.OPCOES_FILTRO_RAPIDO
    assert saida["opcao_por_rotulo"]["Todas as NFs"] is None
    assert all(saida["opcao_por_rotulo"][r] for r in cv.OPCOES_FILTRO_RAPIDO[1:])
    assert saida["aviso"] == "Pagos: 3 NF(s) · valor R$ 1.234.567,80"


def test_atualizar_o_controle_da_vibra_mostra_so_as_colunas_pedidas():
    colunas = {nome: i for i, nome in enumerate(cv.COLUNAS)}
    saida = _rodar_node({"filtros": {"colunas": colunas, "linhas": [], "de": "2026-09-01", "ate": "2026-09-30"}})
    ocultas = {cv._letra(cv.COLUNAS[inicio - 1 + i])
               for inicio, quantidade in saida["ocultas_controle_vibra"] for i in range(quantidade)}
    visiveis = {cv._letra(nome) for nome in cv.COLUNAS} - ocultas
    assert visiveis == set("ACDFGHIJOPRTVW")  # O remessa, R comodato, T NF assinada; S (instalação) não
    # faixas contíguas: uma chamada de ocultar por faixa
    assert saida["ocultas_controle_vibra"] == [[2, 1], [5, 1], [11, 4], [17, 1], [19, 1], [21, 1], [24, 1]]



def test_excel_do_filtrado_tem_so_as_linhas_e_colunas_visiveis(tmp_path):
    import io
    import zipfile

    openpyxl = pytest.importorskip("openpyxl")
    cabecalho = list(cv.COLUNAS)
    linha = [""] * len(cabecalho)
    linha[cv.COLUNAS.index("NF")] = 21903
    linha[cv.COLUNAS.index("Emissão")] = {"data": "2026-05-26"}
    linha[cv.COLUNAS.index("Saldo em aberto (R$)")] = 4561.68
    linha[cv.COLUNAS.index("Filial Vibra (CNPJ)")] = "34.274.233/0001-02"
    linha[cv.COLUNAS.index("Material")] = "1x LICENCIAMENTO & <suporte>"
    linha[cv.COLUNAS.index("Status financeiro")] = "Em aberto"  # coluna oculta: não vai
    linha[cv.COLUNAS.index("Dias em atraso")] = 116
    total = [""] * len(cabecalho)
    total[cv.COLUNAS.index("Série")] = "TOTAL"
    total[cv.COLUNAS.index("Emissão")] = 1
    total[cv.COLUNAS.index("Saldo em aberto (R$)")] = 4561.68
    total[cv.COLUNAS.index("Material")] = 4561.68
    # colunas da visão "Atualizar o controle da Vibra": A C D F G H I J O S T
    colunas = [cv.COLUNAS.index(cv.COLUNAS[ord(l) - 65]) for l in "ACDFGHIJOPRSTVW"]
    partes = _rodar_node({"xlsx": {"cabecalho": cabecalho, "linhas": [linha], "total": total,
                                   "colunas": colunas, "larguras": [70] * len(colunas)}})

    arquivo = io.BytesIO()
    with zipfile.ZipFile(arquivo, "w", zipfile.ZIP_DEFLATED) as zip_:  # o que o Utilities.zip faz
        for caminho, xml in partes.items():
            zip_.writestr(caminho, xml.encode("utf-8"))
    planilha = openpyxl.load_workbook(io.BytesIO(arquivo.getvalue()))
    aba = planilha["Controle Vibra"]
    linhas = [[c.value for c in r] for r in aba.iter_rows()]

    assert linhas[0] == ["NF", "Emissão", "Vencimento", "Saldo em aberto (R$)", "Nº Pedido Vibra",
                         "Filial Vibra (CNPJ)", "Tipo de NF", "Material", "NF de remessa", "Cliente final",
                         "NF comodato Vibra", "Data instalação", "Data entrega / NF assinada", "Obs. financeiro",
                         "Dias em atraso"]
    nf = dict(zip(linhas[0], linhas[1]))
    assert nf["NF"] == 21903 and nf["Saldo em aberto (R$)"] == 4561.68 and nf["Dias em atraso"] == 116
    assert nf["Emissão"] == datetime(2026, 5, 26)
    assert nf["Material"] == "1x LICENCIAMENTO & <suporte>"
    assert "Em aberto" not in linhas[1]
    totais = dict(zip(linhas[0], linhas[2]))
    assert totais["NF"] == "TOTAL" and totais["Emissão"] == 1 and totais["Material"] == 4561.68
    assert len(linhas) == 3  # sem a linha 1 da planilha (filtro rápido e título)
    assert aba["D2"].number_format == '"R$ "#,##0.00'
    assert aba["B2"].number_format == "dd/mm/yyyy"
    assert aba["H3"].number_format == '"Em atraso: R$ "#,##0.00'
    assert aba["A1"].font.b and aba.freeze_panes == "A2"


def test_aguardando_a_emissao_esconde_b_c_d_e_h_k_l_m_n():
    colunas = {nome: i for i, nome in enumerate(cv.COLUNAS)}
    saida = _rodar_node({"filtros": {"colunas": colunas, "linhas": [], "de": "2026-09-01", "ate": "2026-09-30"}})
    ocultas = {cv._letra(cv.COLUNAS[inicio - 1 + i])
               for inicio, quantidade in saida["ocultas_emissao"] for i in range(quantidade)}
    assert ocultas == set("BCDEHKLMN")
    assert saida["ocultas_emissao"] == [[2, 4], [8, 1], [11, 4]]
    assert saida["ocultas_pagos"] is None  # os outros filtros não escondem colunas



def test_listas_e_importacao_do_operacional_iguais_nas_duas_versoes():
    importacao = [
        ["NF", "Série", "NF comodato Vibra", "Obs. Emissão", "Status entrega"],
        ["25263", "2", "2951430", "Enviadas 19/10", "Não instalado"],
        ["21897", "2", "4503751", "Enviadas 19/10", ""],
    ]
    aba = planilha_layout_antigo()
    saida = _rodar_node({"linhas": [_como_bigquery(c) for c in CASOS], "aba": aba, "desde": "2026-01-01",
                         "locais": [], "importacao": importacao})
    assert saida["listas"] == cv.valores_aba_listas()

    manuais = cv.ler_preenchimento_manual(aba)
    tabela = cv.mesclar_manuais(cv.selecionar_linhas(cv.montar_controle(pd.DataFrame(CASOS)), manuais,
                                                     date(2026, 1, 1)),
                                manuais, {}, cv.ler_importacao(importacao))
    largura = len(cv.COLUNAS_DW) + 1 + len(cv.COLUNAS_MANUAIS)
    esperado = [l[:largura] for l in cv.valores_aba_controle(tabela, datetime(2026, 10, 20))[2:2 + len(tabela)]]
    assert saida["linhas"] == esperado
    por_nf = {str(l[0]): l for l in esperado}
    col = cv.COLUNAS.index
    assert por_nf["25263"][col("NF comodato Vibra")] == "2951430"
    assert por_nf["25263"][col("Obs. Emissão")] == "Enviadas 19/10"
    # a importação só preenche o que está em branco: a 25263 é nova e já entrou como "A validar"
    assert por_nf["25263"][col("Status entrega")] == "A validar"
    # 21897 já estava na planilha com "Aguardando NF assinada": migra pra etapa nova
    assert por_nf["21897"][col("Status entrega")] == "Aguardando cliente assinar comodato"
    assert por_nf["21897"][col("NF comodato Vibra")] == "4503751"
