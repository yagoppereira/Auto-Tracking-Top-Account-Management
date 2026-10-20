/**
 * Controle de NFs da Vibra: Apps Script vinculado à planilha PENDENCIAS VIBRA.
 *
 * Consulta o DW (BigQuery) com o login de quem executa, monta a lista de
 * NFs emitidas para a Vibra e grava na aba "Controle NFs Vibra":
 * - colunas A a N ("Do CIGAM") são reescritas a cada execução;
 * - coluna O (cliente final) é fórmula da planilha (linha 2) e não é tocada:
 *   procura a filial Vibra + o nº do local de entrega da NF na aba "Locais
 *   de entrega"; local que aparece nas NFs e não está lá é acrescentado no
 *   fim da aba pra alguém preencher;
 * - colunas P a S (status de entrega, data, observações) são do time: o
 *   script lê o que já está lá e devolve cada valor na linha da mesma NF
 *   (chave = NF + série);
 * - colunas T e U são fórmulas da própria planilha (linha 2) e não são tocadas.
 *
 * Menu "Controle Vibra" na planilha: Atualizar agora, Agendar atualização
 * diária e Remover agendamento. O agendamento roda com o login de quem o
 * criou.
 *
 * É o espelho de controle_vibra.py (repositório Auto-Tracking-Top-Account-Management):
 * mesmo SQL e mesmas regras. tests/test_apps_script.py compara as duas
 * versões; ao mudar uma, mude a outra.
 */

const PROJECT_ID = 'hip-bonito-453017-m2';
const RAIZ_CNPJ_VIBRA = '34274233';
const PISO_CONSULTA = '2024-01-01';
const DESDE_PADRAO = '2026-01-01';
const ABA_CONTROLE = 'Controle NFs Vibra';
const ABA_FONTES = 'Fontes';
const ABA_LOCAIS = 'Locais de entrega';
const ABA_LISTAS = 'Listas';
// aba opcional pra trazer pra dentro do controle o que o operacional anotou
// fora dele (NF, Série + colunas com o nome das colunas manuais): preenche só
// o que está em branco no controle e é limpa depois de usada
const ABA_IMPORTACAO = 'Importar operacional';
const HORA_AGENDAMENTO = 7;

const STATUS_EM_ABERTO = 'Em aberto';
const STATUS_PAGO_PARCIAL = 'Pago parcial';
const STATUS_PAGO = 'Pago';
const STATUS_DEVOLVIDA = 'Devolvida';
const STATUS_ABATIDA = 'Abatida';
const STATUS_CANCELADA = 'Cancelada';
const STATUS_PERDA = 'Baixa por perda';
const STATUS_SEM_TITULO = 'Sem título';
const STATUS_ABERTOS = [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL, STATUS_SEM_TITULO];

const SIGNIFICADO_STATUS = [
  [STATUS_EM_ABERTO, 'Título sem nenhuma baixa no CIGAM.'],
  [STATUS_PAGO_PARCIAL, 'Título com parte do valor recebida; o saldo segue em aberto.'],
  [STATUS_PAGO, 'Título liquidado por recebimento.'],
  [STATUS_DEVOLVIDA, 'Título baixado por abatimento (portador X30) e com NF de devolução da Vibra.'],
  [STATUS_ABATIDA, 'Título baixado por abatimento (portador X30), sem NF de devolução.'],
  [STATUS_CANCELADA, 'NF cancelada no CIGAM.'],
  [STATUS_PERDA, 'Título baixado por perda (portadores X90 a X99).'],
  [STATUS_SEM_TITULO, 'NF confirmada sem título a receber no DW (o DW atualiza com até 1 dia de atraso).'],
];

// status de entrega (Emissão/operacional): entrega -> instalação -> comodato
// da Vibra assinado pelo cliente final. O Financeiro só cobra com o comodato
// assinado. Mesma lista de controle_vibra.py; vai pra aba Listas a cada
// atualização (lista suspensa e próxima ação).
const STATUS_ENTREGA_ACAO = [
  ['A validar', 'Emissão: confirmar entrega'],
  ['Não entregue', 'Emissão: acompanhar entrega'],
  ['Não instalado', 'Emissão: acompanhar instalação'],
  ['Não instalado, comodato assinado', 'Financeiro: cobrar Vibra'],
  ['Aguardando comodato da Vibra', 'Emissão: cobrar comodato da Vibra'],
  ['Aguardando cliente assinar comodato', 'Emissão: obter assinatura do comodato'],
  ['Comodato assinado', 'Financeiro: cobrar Vibra'],
  ['Problema na entrega', 'Emissão: resolver entrega'],
  ['Não se aplica', 'Financeiro: cobrar Vibra'],
];
// material já entregue (base dos filtros "entregues" e da visão da Vibra)
const STATUS_ENTREGUES = ['Não instalado', 'Não instalado, comodato assinado', 'Aguardando comodato da Vibra',
  'Aguardando cliente assinar comodato', 'Comodato assinado', 'Não se aplica'];
// ainda tem etapa do operacional (filtro "Aguardando a Emissão"); as que já
// estão ok para cobrança ("Financeiro: cobrar Vibra") ficam de fora
const STATUS_PENDENTES_OPERACIONAL = ['A validar', 'Não entregue', 'Não instalado', 'Aguardando comodato da Vibra', 'Aguardando cliente assinar comodato', 'Problema na entrega'];
// status da lista anterior -> etapa equivalente da esteira
const MIGRACAO_STATUS_ENTREGA = {
  'Aguardando entrega': 'Não entregue',
  'Aguardando NF assinada': 'Aguardando cliente assinar comodato',
  'NF assinada recebida': 'Comodato assinado',
};

const COLUNAS_DW = [
  'NF', 'Série', 'Emissão', 'Vencimento', 'Valor (R$)', 'Saldo em aberto (R$)',
  'Nº Pedido Vibra', 'Filial Vibra (CNPJ)', 'Tipo de NF', 'Material',
  'Status financeiro', 'Data pagamento / baixa', 'NF de devolução', 'Local de entrega (nº)',
  'NF de remessa',
];
const CABECALHO_LOCAIS = [
  'Filial Vibra (CNPJ)', 'Local de entrega (nº)', 'Cliente final', 'Cód. CIGAM do cliente final', 'Origem',
];
// O é fórmula (cliente final pela aba Locais de entrega); P a S são do time
const COLUNA_CLIENTE_FINAL = 'Cliente final';
const COLUNAS_MANUAIS = ['Status entrega', 'NF comodato Vibra', 'Data instalação', 'Data entrega / NF assinada',
  'Obs. Emissão', 'Obs. financeiro'];
// coluna que a Emissão preenchia à mão antes de virar fórmula: o que foi
// digitado e não bate com a aba Locais de entrega vai pra Obs. Emissão
const COLUNA_CLIENTE_FINAL_DIGITADO = 'Cliente final / local de entrega';
const PRIMEIRA_LINHA_DADOS = 3;
const COLUNAS_PLANILHA = COLUNAS_DW.concat([COLUNA_CLIENTE_FINAL], COLUNAS_MANUAIS, ['Dias em atraso', 'Próxima ação']);
const VERDE_PLANILHA = '#356854';

// Linha de totais logo abaixo da última NF (mesmo conteúdo de linha_total em
// controle_vibra.py). SUBTOTAL só conta as linhas visíveis no filtro; "em
// atraso" aplica o SUBTOTAL célula a célula com MAP. A coluna A fica vazia
// pras fórmulas de O, T e U (ARRAYFORMULA da linha 2) não calcularem nela.
const MARCA_LINHA_TOTAL = 'TOTAL';
const FORMATOS_LINHA_TOTAL = {
  'Emissão': '0" NF(s)"',
  'Valor (R$)': '[$R$ -416]#,##0.00',
  'Saldo em aberto (R$)': '[$R$ -416]#,##0.00',
  'Filial Vibra (CNPJ)': '0" NF(s) em atraso"',
  'Material': '"Em atraso: "[$R$ -416]#,##0.00',
};
const TOLERANCIA_CENTAVOS = 0.01;

const TABELAS_CONSULTADAS = [
  'bronze.cigam__empresas', 'bronze.cigam__notas_fiscais',
  'silver.lancamentos_receber', 'silver.cadastro_materiais_cigam',
];

// mesmo texto de SQL_NOTAS_VIBRA em controle_vibra.py
const SQL_NOTAS_VIBRA = `
WITH vibra AS (
  SELECT codigo, REGEXP_REPLACE(cnpjCpf, r'\\D', '') AS cnpj
  FROM \`${PROJECT_ID}.bronze.cigam__empresas\`
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
  FROM \`${PROJECT_ID}.bronze.cigam__notas_fiscais\` n
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
  FROM \`${PROJECT_ID}.bronze.cigam__notas_fiscais\`
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
  FROM \`${PROJECT_ID}.bronze.cigam__notas_fiscais\`
  WHERE dataEmissao >= @piso AND cliente IN (SELECT codigo FROM vibra)
    AND especieNota = 'E' AND tipoNota IN ('D', 'C') AND status = '3'
  GROUP BY 1, 2, 3
),
lanc AS (
  SELECT nf, serie, codigoEmpresa, codigoTipo, codigoLancamento, codigoPartida, codigoPortador,
         SAFE.PARSE_DATE('%d/%m/%Y', dataVencimento) AS vencimento,
         SAFE.PARSE_DATE('%d/%m/%Y', data) AS data,
         SAFE_CAST(valor AS NUMERIC) AS valor
  FROM \`${PROJECT_ID}.silver.lancamentos_receber\`
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
  LEFT JOIN \`${PROJECT_ID}.silver.cadastro_materiais_cigam\` m ON m.codigo_material = i.material
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
LEFT JOIN \`${PROJECT_ID}.bronze.cigam__empresas\` e ON e.codigo = r.cliente_remessa
LEFT JOIN titulos t ON t.nf = n.nf AND t.serie = n.serie AND t.codigoEmpresa = n.cliente
LEFT JOIN devolucoes d ON d.nf = n.nf AND d.serie = n.serie AND d.cliente = n.cliente
LEFT JOIN itens_nf i ON i.nf = n.nf AND i.serie = n.serie AND i.cliente = n.cliente
WHERE n.especieNota = 'S' OR t.nf IS NOT NULL
`;


// ============================================================================
// Menu e agendamento
// ============================================================================
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Controle Vibra')
    .addItem('Atualizar agora', 'atualizarControleVibra')
    .addItem('Filtros', 'abrirFiltros')
    .addItem('Baixar em Excel o que está na tela', 'abrirDownloadExcel')
    .addSeparator()
    .addItem('Agendar atualização diária', 'agendarAtualizacaoDiaria')
    .addItem('Remover agendamento', 'removerAgendamento')
    .addSeparator()
    .addItem('Criar filtro rápido e botão na linha 1', 'criarFiltroRapido')
    .addToUi();
}

function agendarAtualizacaoDiaria() {
  removerAgendamento();
  ScriptApp.newTrigger('atualizarControleVibra').timeBased().everyDays(1).atHour(HORA_AGENDAMENTO)
    .inTimezone(fusoDaPlanilha_()).create();
  SpreadsheetApp.getActive().toast(
    `Atualização diária agendada para ${HORA_AGENDAMENTO}h, com o seu login.`, 'Controle Vibra', 8);
}

/** Horário de Brasília da planilha, e não o fuso do projeto do Apps Script,
 * que só é São Paulo se o appsscript.json deste diretório foi colado (com o
 * padrão, a hora de "atualizado em" e do agendamento saía deslocada). */
function fusoDaPlanilha_() {
  return SpreadsheetApp.getActive().getSpreadsheetTimeZone() || 'America/Sao_Paulo';
}

function removerAgendamento() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'atualizarControleVibra')
    .forEach(t => ScriptApp.deleteTrigger(t));
}


// ============================================================================
// Execução
// ============================================================================
function atualizarControleVibra() {
  const trava = LockService.getDocumentLock();
  if (!trava.tryLock(30000)) {
    throw new Error('Já existe uma atualização do controle Vibra em andamento.');
  }
  try {
    const planilha = SpreadsheetApp.getActive();
    const aba = planilha.getSheetByName(ABA_CONTROLE);
    if (!aba) throw new Error(`Aba "${ABA_CONTROLE}" não encontrada.`);

    let abaLocais = planilha.getSheetByName(ABA_LOCAIS);
    if (!abaLocais) {
      abaLocais = planilha.insertSheet(ABA_LOCAIS);
      abaLocais.getRange(1, 1, 1, CABECALHO_LOCAIS.length).setValues([CABECALHO_LOCAIS]);
    }
    const valoresLocais = abaLocais.getDataRange().getValues();

    migrarLayout_(aba);
    escreverListas_(planilha);
    const abaImportacao = planilha.getSheetByName(ABA_IMPORTACAO);
    const valoresImportacao = abaImportacao ? abaImportacao.getDataRange().getValues() : [];
    const atual = aba.getDataRange().getValues();
    const tabela = montarTabela(consultarNotasVibra_(), atual, DESDE_PADRAO, valoresLocais, valoresImportacao);
    const agora = new Date();
    gravarControle_(aba, tabela, agora, atual);
    atualizarFontes_(planilha, agora, tabela.length);
    const novos = locaisSemCadastro(tabela, lerLocaisEntrega(valoresLocais));
    if (novos.length) {
      abaLocais.getRange(abaLocais.getLastRow() + 1, 1, novos.length, CABECALHO_LOCAIS.length).setValues(novos);
    }
    const importadas = Object.keys(lerImportacao(valoresImportacao)).length;
    if (importadas && abaImportacao.getLastRow() > 1) {  // usada: limpa os dados, fica o cabeçalho
      abaImportacao.getRange(2, 1, abaImportacao.getLastRow() - 1, abaImportacao.getLastColumn()).clearContent();
    }
    planilha.toast(
      `${tabela.length} NFs atualizadas a partir do CIGAM.` +
      (novos.length ? ` ${novos.length} local(is) de entrega novo(s) na aba "${ABA_LOCAIS}".` : '') +
      (importadas ? ` ${importadas} NF(s) completadas pela aba "${ABA_IMPORTACAO}".` : ''),
      'Controle Vibra', 10);
  } finally {
    trava.releaseLock();
  }
}

/** Roda SQL_NOTAS_VIBRA com o login de quem executa e devolve uma lista de
 * objetos {coluna: valor}. O BigQuery devolve tudo como texto (DATE como
 * "2026-01-14", NUMERIC como "35861.37", BOOL como "true"). */
function consultarNotasVibra_() {
  const pedido = {
    query: SQL_NOTAS_VIBRA,
    useLegacySql: false,
    parameterMode: 'NAMED',
    queryParameters: [
      { name: 'raiz_cnpj', parameterType: { type: 'STRING' }, parameterValue: { value: RAIZ_CNPJ_VIBRA } },
      { name: 'piso', parameterType: { type: 'DATE' }, parameterValue: { value: PISO_CONSULTA } },
    ],
    timeoutMs: 60000,
  };
  // sem o serviço avançado ligado, o nome BigQuery nem existe no script: não
  // é falta de permissão no DW, é configuração do projeto do Apps Script
  if (typeof BigQuery === 'undefined') {
    throw new Error(
      'O serviço BigQuery não está ativado neste Apps Script. No editor (Extensões > Apps Script), '
      + 'clique em Serviços (+), escolha "BigQuery API" e clique em Adicionar (identificador BigQuery). '
      + 'Ou cole o appsscript.json do repositório no lugar do manifesto do projeto.');
  }
  let resposta;
  try {
    resposta = BigQuery.Jobs.query(pedido, PROJECT_ID);
  } catch (erro) {
    throw new Error(
      'Não consegui consultar o DW com o seu login. É preciso poder rodar consultas no projeto ' +
      `${PROJECT_ID} e ler ${TABELAS_CONSULTADAS.join(', ')}. Detalhe: ${erro.message}`);
  }
  const job = resposta.jobReference;
  while (!resposta.jobComplete) {
    Utilities.sleep(1000);
    resposta = BigQuery.Jobs.getQueryResults(PROJECT_ID, job.jobId, { location: job.location });
  }
  let linhas = resposta.rows || [];
  let pagina = resposta.pageToken;
  while (pagina) {
    const proxima = BigQuery.Jobs.getQueryResults(PROJECT_ID, job.jobId, { location: job.location, pageToken: pagina });
    linhas = linhas.concat(proxima.rows || []);
    pagina = proxima.pageToken;
  }
  const campos = resposta.schema.fields.map(c => c.name);
  return linhas.map(linha => {
    const registro = {};
    linha.f.forEach((celula, i) => { registro[campos[i]] = celula.v; });
    return registro;
  });
}

function gravarControle_(aba, tabela, agora, anterior) {
  const nDw = COLUNAS_DW.length;
  const nColunas = nDw + 1 + COLUNAS_MANUAIS.length;
  const totaisAntigos = linhasDeTotal_(aba);
  // grava sem filtro: linha oculta pelo filtro perde as datas no setValues
  // (aconteceu com "Pagos no período" aplicado: as NFs em aberto ficaram sem
  // emissão e vencimento, e os dias em atraso viraram 46 mil). ajustarFiltro_
  // recria o filtro no fim e reaplica o filtro rápido de A1.
  if (aba.getFilter()) aba.getFilter().remove();
  const necessarias = PRIMEIRA_LINHA_DADOS + tabela.length;  // NFs + linha de totais
  if (aba.getMaxRows() < necessarias) {
    aba.insertRowsAfter(aba.getMaxRows(), necessarias - aba.getMaxRows());
  }
  const sobra = aba.getMaxRows() - PRIMEIRA_LINHA_DADOS + 1;
  // a lista suspensa e as datas com validação rígida recusam valor no
  // setValues (aconteceu com a NF comodato na coluna que herdou a lista do
  // Status entrega): grava sem validação e reaplica as do layout no fim. Se
  // a gravação falhar mesmo assim, a aba volta ao que estava antes.
  aba.getRange(PRIMEIRA_LINHA_DADOS, nDw + 2, sobra, COLUNAS_MANUAIS.length).clearDataValidations();
  try {
    aba.getRange(PRIMEIRA_LINHA_DADOS, 1, sobra, nColunas).clearContent();
    if (tabela.length) {
      // a coluna do cliente final fica vazia pro ARRAYFORMULA da linha 2 se espalhar
      const linhas = linhasParaPlanilha(tabela);
      aba.getRange(PRIMEIRA_LINHA_DADOS, 1, linhas.length, nDw).setValues(linhas.map(l => l.slice(0, nDw)));
      aba.getRange(PRIMEIRA_LINHA_DADOS, nDw + 2, linhas.length, COLUNAS_MANUAIS.length)
        .setValues(linhas.map(l => l.slice(nDw + 1)));
    }
  } catch (erro) {
    const nfs = restaurarAnterior_(aba, anterior);
    aplicarValidacoesManuais_(aba, Math.max(nfs, 1));
    if (nfs) gravarLinhaTotal_(aba, nfs);
    ajustarFiltro_(aba, PRIMEIRA_LINHA_DADOS + Math.max(nfs, 1) - 1);
    throw new Error(`Não consegui gravar o controle (${erro.message}). A aba voltou ao que estava antes `
      + 'da atualização; nada do que o time preencheu se perdeu.');
  }
  aplicarValidacoesManuais_(aba, Math.max(tabela.length, 1));
  const linhaTotalNova = PRIMEIRA_LINHA_DADOS + tabela.length;
  // linha de totais anterior que agora é NF (ou sobra) volta ao formato das NFs
  totaisAntigos.filter(r => r !== linhaTotalNova && r !== PRIMEIRA_LINHA_DADOS).forEach(r =>
    aba.getRange(PRIMEIRA_LINHA_DADOS, 1, 1, COLUNAS_PLANILHA.length)
      .copyFormatToRange(aba, 1, COLUNAS_PLANILHA.length, r, r));
  if (tabela.length) gravarLinhaTotal_(aba, tabela.length);
  ajustarFiltro_(aba, linhaTotalNova - 1);
  // o título fica em A1 ou, com o filtro rápido montado (FiltrosVibra.gs), em G1
  const linha1 = aba.getRange(1, 1, 1, aba.getLastColumn()).getDisplayValues()[0];
  const colunaTitulo = Math.max(linha1.findIndex(v => String(v).startsWith('Do CIGAM')), 0) + 1;
  aba.getRange(1, colunaTitulo).setValue(
    `Do CIGAM (extrator, atualizado em ${Utilities.formatDate(agora, fusoDaPlanilha_(), 'dd/MM/yyyy HH:mm')})`);
}

/** Volta as colunas do CIGAM (A até a NF de remessa) e as do time ao que
 * estava na aba antes da atualização (`anterior` = getValues da aba, já no
 * layout atual) e devolve quantas NFs voltaram. O cliente final é fórmula e
 * não é tocado; a linha de totais é refeita por quem chama. */
function restaurarAnterior_(aba, anterior) {
  const nDw = COLUNAS_DW.length;
  const linhas = (anterior || []).slice(PRIMEIRA_LINHA_DADOS - 1).filter(l => texto_(l[0]));
  if (!linhas.length) return 0;
  const largura = (l, inicio, n) => Array.from({ length: n }, (_, i) => (l[inicio + i] === undefined ? '' : l[inicio + i]));
  aba.getRange(PRIMEIRA_LINHA_DADOS, 1, linhas.length, nDw).setValues(linhas.map(l => largura(l, 0, nDw)));
  aba.getRange(PRIMEIRA_LINHA_DADOS, nDw + 2, linhas.length, COLUNAS_MANUAIS.length)
    .setValues(linhas.map(l => largura(l, nDw + 1, COLUNAS_MANUAIS.length)));
  return linhas.length;
}

/** Validações das colunas do time nas linhas das NFs: lista suspensa do
 * Status entrega (aba Listas) e data nas colunas de data. */
function aplicarValidacoesManuais_(aba, qtd) {
  const coluna = nome => COLUNAS_PLANILHA.indexOf(nome) + 1;
  const faixa = nome => aba.getRange(PRIMEIRA_LINHA_DADOS, coluna(nome), qtd, 1);
  faixa('Status entrega').setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInRange(SpreadsheetApp.getActive().getRange(`${ABA_LISTAS}!$A$2:$A$20`), true)
    .setAllowInvalid(false).build());
  ['Data instalação', 'Data entrega / NF assinada'].forEach(nome => faixa(nome).setDataValidation(
    SpreadsheetApp.newDataValidation().requireDate().setAllowInvalid(false)
      .setHelpText('Informe uma data (dd/mm/aaaa)').build()));
}

/** Linha de totais pra `qtd` NFs (linhas 3 em diante), com o separador de
 * argumentos informado. */
function linhaTotal(qtd, separador) {
  const s = separador;
  const p = PRIMEIRA_LINHA_DADOS;
  const u = PRIMEIRA_LINHA_DADOS + qtd - 1;
  const faixa = nome => `${letraDe_(nome)}${p}:${letraDe_(nome)}${u}`;
  const atraso = faixa('Dias em atraso');
  const linha = COLUNAS_PLANILHA.map(() => '');
  const em = nome => COLUNAS_PLANILHA.indexOf(nome);
  linha[em('Série')] = MARCA_LINHA_TOTAL;
  linha[em('Emissão')] = `=SUBTOTAL(103${s}${faixa('NF')})`;
  linha[em('Valor (R$)')] = `=SUBTOTAL(109${s}${faixa('Valor (R$)')})`;
  linha[em('Saldo em aberto (R$)')] = `=SUBTOTAL(109${s}${faixa('Saldo em aberto (R$)')})`;
  linha[em('Filial Vibra (CNPJ)')] =
    `=SUM(MAP(${faixa('NF')}${s}${atraso}${s}LAMBDA(n${s}d${s}IF(d>0${s}SUBTOTAL(103${s}n)${s}0))))`;
  linha[em('Material')] =
    `=SUM(MAP(${faixa('Saldo em aberto (R$)')}${s}${atraso}${s}LAMBDA(v${s}d${s}IF(d>0${s}SUBTOTAL(109${s}v)${s}0))))`;
  return linha;
}

function letraDe_(nome) {
  let letra = '';
  for (let n = COLUNAS_PLANILHA.indexOf(nome) + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    letra = String.fromCharCode(65 + ((n - 1) % 26)) + letra;
  }
  return letra;
}

/** Grava e formata a linha de totais. As fórmulas vão primeiro com "," (o
 * formato do Apps Script); se a planilha não aceitar, com ";" (pt_BR). */
function gravarLinhaTotal_(aba, qtd) {
  const r = PRIMEIRA_LINHA_DADOS + qtd;
  const nDw = COLUNAS_DW.length;  // as fórmulas ficam de B a J, dentro de A a N
  const faixa = aba.getRange(r, 1, 1, nDw);
  faixa.setValues([linhaTotal(qtd, ',').slice(0, nDw)]);
  SpreadsheetApp.flush();
  if (faixa.getDisplayValues()[0].some(v => String(v).startsWith('#'))) {
    faixa.setValues([linhaTotal(qtd, ';').slice(0, nDw)]);
  }
  aba.getRange(r, 1, 1, COLUNAS_PLANILHA.length).clearDataValidations()
    .setBackground('#fff2cc').setFontWeight('bold')
    .setBorder(true, null, null, null, null, null, VERDE_PLANILHA, SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  Object.keys(FORMATOS_LINHA_TOTAL).forEach(nome =>
    aba.getRange(r, COLUNAS_PLANILHA.indexOf(nome) + 1)
      .setNumberFormat(FORMATOS_LINHA_TOTAL[nome]).setHorizontalAlignment('right'));
}

/** Linhas (a partir de 1) que têm a marca da linha de totais na coluna B. */
function linhasDeTotal_(aba) {
  const n = aba.getMaxRows() - PRIMEIRA_LINHA_DADOS + 1;
  if (n <= 0) return [];
  return aba.getRange(PRIMEIRA_LINHA_DADOS, COLUNAS_PLANILHA.indexOf('Série') + 1, n, 1).getValues()
    .map((v, i) => (texto_(v[0]) === MARCA_LINHA_TOTAL ? PRIMEIRA_LINHA_DADOS + i : null))
    .filter(r => r !== null);
}

/** O filtro da aba vai do cabeçalho até a última NF: a linha de totais fica
 * fora, senão o próprio filtro a esconderia. Se a faixa mudou, recria o
 * filtro e reaplica o filtro rápido escolhido em A1 (FiltrosVibra.gs). */
function ajustarFiltro_(aba, ultimaLinhaNf) {
  const ultima = Math.max(ultimaLinhaNf, PRIMEIRA_LINHA_DADOS);
  const filtro = aba.getFilter();
  if (filtro) {
    const faixa = filtro.getRange();
    if (faixa.getRow() === PRIMEIRA_LINHA_DADOS - 1 && faixa.getLastRow() === ultima) return;
    filtro.remove();
  }
  aba.getRange(PRIMEIRA_LINHA_DADOS - 1, 1, ultima - PRIMEIRA_LINHA_DADOS + 2,
    Math.max(aba.getLastColumn(), COLUNAS_PLANILHA.length)).createFilter();
  if (typeof aplicarFiltroRapido_ === 'function' && filtroRapidoMontado_(aba)) aplicarFiltroRapido_(aba);
}

/** Planilha no layout anterior à coluna "NF de remessa": insere a coluna
 * depois do local de entrega. Inserir (e não regravar) leva junto as
 * fórmulas, filtros, formatos e mesclagens das colunas da direita. */
function migrarLayout_(aba) {
  const linhaCabecalho = PRIMEIRA_LINHA_DADOS - 1;
  const cabecalho = () => aba.getRange(linhaCabecalho, 1, 1, aba.getLastColumn()).getDisplayValues()[0];
  // coluna nova logo depois de `depoisDe`, se ainda não existe. A coluna
  // inserida herda o formato e a lista suspensa da vizinha da esquerda: os
  // dados ficam sem validação e, se `data`, com formato e validação de data.
  const garantir = (nome, depoisDe, largura, data) => {
    const atual = cabecalho();
    const posicao = atual.indexOf(depoisDe);
    if (atual.indexOf(nome) >= 0 || posicao < 0) return;
    aba.insertColumnAfter(posicao + 1);
    const coluna = posicao + 2;
    aba.getRange(linhaCabecalho, coluna).setValue(nome);
    aba.setColumnWidth(coluna, largura);
    const dados = aba.getRange(PRIMEIRA_LINHA_DADOS, coluna, aba.getMaxRows() - PRIMEIRA_LINHA_DADOS + 1, 1);
    dados.clearDataValidations().setNumberFormat(data ? 'dd/mm/yyyy' : '@').setHorizontalAlignment('center');
    if (data) {
      dados.setDataValidation(SpreadsheetApp.newDataValidation().requireDate().setAllowInvalid(false)
        .setHelpText('Informe uma data (dd/mm/aaaa)').build());
    }
  };
  // "Situação NF assinada" foi criada por uma versão intermediária do script
  // e trocada por "Data instalação": sai se estiver vazia
  const obsoleta = cabecalho().indexOf('Situação NF assinada');
  if (obsoleta >= 0) {
    const n = Math.max(aba.getLastRow() - linhaCabecalho, 1);
    const vazia = aba.getRange(linhaCabecalho + 1, obsoleta + 1, n, 1).getDisplayValues().every(l => !l[0]);
    if (vazia) aba.deleteColumn(obsoleta + 1);
  }
  garantir('NF de remessa', 'Local de entrega (nº)', 95, false);
  // colunas da planilha do operacional, no bloco da Emissão
  garantir('NF comodato Vibra', 'Status entrega', 120, false);
  garantir('Data instalação', 'NF comodato Vibra', 110, true);
  // NF comodato é texto livre: tira a lista suspensa que a coluna possa ter
  // herdado do Status entrega numa migração anterior
  const comodato = cabecalho().indexOf('NF comodato Vibra');
  if (comodato >= 0) {
    aba.getRange(PRIMEIRA_LINHA_DADOS, comodato + 1, aba.getMaxRows() - PRIMEIRA_LINHA_DADOS + 1, 1)
      .clearDataValidations().setNumberFormat('@');
  }
}

/** Aba Listas: status de entrega com a próxima ação (lista suspensa e
 * VLOOKUP da coluna Próxima ação) e o significado dos status financeiros. */
function escreverListas_(planilha) {
  const listas = planilha.getSheetByName(ABA_LISTAS) || planilha.insertSheet(ABA_LISTAS);
  const valores = valoresAbaListas();
  listas.getRange(1, 1, Math.max(listas.getMaxRows(), valores.length), 5).clearContent();
  listas.getRange(1, 1, valores.length, 5).setValues(valores);
}

function valoresAbaListas() {
  const linhas = [['Status entrega', 'Próxima ação (se em aberto)', '', 'Status financeiro', 'O que significa']];
  const total = Math.max(STATUS_ENTREGA_ACAO.length, SIGNIFICADO_STATUS.length);
  for (let i = 0; i < total; i++) {
    const entrega = STATUS_ENTREGA_ACAO[i] || ['', ''];
    const status = SIGNIFICADO_STATUS[i] || ['', ''];
    linhas.push([entrega[0], entrega[1], '', status[0], status[1]]);
  }
  for (let i = 0; i < 3; i++) linhas.push(['', '', '', '', '']);
  return linhas;
}

function atualizarFontes_(planilha, agora, linhas) {
  const fontes = planilha.getSheetByName(ABA_FONTES);
  if (!fontes) return;
  const quem = Session.getEffectiveUser().getEmail() || 'agendamento';
  fontes.getRange('D2').setValue('Apps Script da planilha (ControleVibra.gs), com o login de quem executa');
  fontes.getRange('G2:I2').setValues([[
    "'" + Utilities.formatDate(agora, fusoDaPlanilha_(), 'dd/MM/yyyy HH:mm'), quem, linhas]]);
}


// ============================================================================
// Regras (espelho de controle_vibra.py)
// ============================================================================
function vazio_(valor) {
  return valor === null || valor === undefined || (typeof valor === 'number' && isNaN(valor));
}

function texto_(valor) {
  return vazio_(valor) ? '' : String(valor).trim();
}

function numero_(valor) {
  const n = parseFloat(valor);
  return isNaN(n) ? 0 : n;
}

function inteiro_(valor) {
  const n = parseInt(texto_(valor), 10);
  return isNaN(n) ? null : n;
}

function verdadeiro_(valor) {
  return valor === true || valor === 'true';
}

function arredondar_(valor) {
  return Math.round(valor * 100) / 100;
}

/** Date local à meia-noite a partir de Date, "aaaa-mm-dd" ou "dd/mm/aaaa". */
function data_(valor) {
  if (vazio_(valor)) return null;
  if (valor instanceof Date) return new Date(valor.getFullYear(), valor.getMonth(), valor.getDate());
  const texto = String(valor).trim();
  let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(texto);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  m = /^(\d{2})\/(\d{2})\/(\d{4})/.exec(texto);
  if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
  return null;
}

function classificarStatus(linha) {
  if (texto_(linha.status) === '5') {
    return { status: STATUS_CANCELADA, data: data_(linha.data_cancelamento), saldo: 0 };
  }
  if (vazio_(linha.valor_titulo)) {
    return { status: STATUS_SEM_TITULO, data: null, saldo: null };
  }
  const valorTitulo = numero_(linha.valor_titulo);
  const pago = numero_(linha.pago);
  const abatido = numero_(linha.abatido);
  const perda = numero_(linha.perda);
  const baixado = pago + abatido + perda;
  const saldo = arredondar_(Math.max(valorTitulo - baixado, 0));
  const dataBaixa = data_(linha.data_baixa);

  if (saldo > TOLERANCIA_CENTAVOS) {
    if (baixado > TOLERANCIA_CENTAVOS) return { status: STATUS_PAGO_PARCIAL, data: dataBaixa, saldo };
    return { status: STATUS_EM_ABERTO, data: null, saldo };
  }
  if (perda > TOLERANCIA_CENTAVOS) return { status: STATUS_PERDA, data: dataBaixa, saldo: 0 };
  if (pago > TOLERANCIA_CENTAVOS) return { status: STATUS_PAGO, data: dataBaixa, saldo: 0 };
  if (texto_(linha.nfs_devolucao)) {
    return { status: STATUS_DEVOLVIDA, data: data_(linha.data_devolucao) || dataBaixa, saldo: 0 };
  }
  return { status: STATUS_ABATIDA, data: dataBaixa, saldo: 0 };
}

function classificarTipo(linha) {
  const serie = texto_(linha.serie).toUpperCase();
  if (serie.startsWith('S')) return verdadeiro_(linha.tem_licenciamento) ? 'Mensalidade' : 'Serviço';
  if (verdadeiro_(linha.tem_cta)) return 'Venda de CTA';
  if (verdadeiro_(linha.tem_sonda)) return 'Venda de Sonda';
  return 'Peças e periféricos';
}

function extrairPedidoVibra(textos) {
  const achados = texto_(textos).match(/(?<!\d)(4[56]\d{8})(?!\d)/g) || [];
  return Array.from(new Set(achados)).join(', ');
}

function formatarCnpj(digitos) {
  const d = texto_(digitos).replace(/\D/g, '');
  if (d.length !== 14) return d;
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

function montarControle(linhasDw) {
  return linhasDw.map(linha => {
    const st = classificarStatus(linha);
    const totalNf = numero_(linha.total_nf) || numero_(linha.valor_titulo);
    return {
      'NF': texto_(linha.nf),
      'Série': texto_(linha.serie),
      'Emissão': data_(linha.emissao),
      'Vencimento': data_(linha.vencimento),
      'Valor (R$)': arredondar_(totalNf),
      'Saldo em aberto (R$)': st.saldo,
      'Nº Pedido Vibra': extrairPedidoVibra(linha.textos),
      'Filial Vibra (CNPJ)': formatarCnpj(linha.cnpj),
      'Tipo de NF': classificarTipo(linha),
      'Material': texto_(linha.material),
      'Status financeiro': st.status,
      'Data pagamento / baixa': st.data,
      'NF de devolução': texto_(linha.nfs_devolucao),
      'Local de entrega (nº)': inteiro_(linha.local_entrega),
      'NF de remessa': texto_(linha.nf_remessa),
      _clienteRemessa: nomeClienteRemessa(linha.nome_remessa, linha.municipio_remessa, linha.uf_remessa),
      _codigoRemessa: texto_(linha.cliente_remessa),
    };
  });
}

/** "CIA. DE FERRO LIGAS DA BAHIA - FERBASA (Pojuca/BA)": o cliente da NF de
 * remessa no formato da aba Locais de entrega. */
function nomeClienteRemessa(nome, municipio, uf) {
  nome = texto_(nome);
  if (!nome) return '';
  const cidade = texto_(municipio).split(/\s+/).filter(p => p)
    .map(p => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase()).join(' ');
  const local = [cidade, texto_(uf).toUpperCase()].filter(x => x).join('/');
  return local ? `${nome} (${local})` : nome;
}

function chaveNf(nf, serie) {
  let numero = texto_(nf).replace(/\D/g, '');
  numero = numero.replace(/^0+/, '') || numero;
  return `${numero}|${texto_(serie).toUpperCase()}`;
}

/** {chave: {coluna manual: valor}} a partir dos valores da aba. Acha o
 * cabeçalho pela linha cuja primeira célula é "NF" e as colunas pelo nome.
 * Data digitada volta como Date (getValues), texto como texto. */
function lerPreenchimentoManual(valoresAba) {
  const indiceCabecalho = (valoresAba || []).slice(0, 10).findIndex(l => l && texto_(l[0]) === 'NF');
  if (indiceCabecalho < 0) return {};
  const cabecalho = valoresAba[indiceCabecalho].map(texto_);
  const posicoes = {};
  const lidas = COLUNAS_MANUAIS.concat(['Nº Pedido Vibra', COLUNA_CLIENTE_FINAL_DIGITADO]);
  lidas.concat(['Série']).forEach(nome => {
    const pos = cabecalho.indexOf(nome);
    if (pos >= 0) posicoes[nome] = pos;
  });
  if (!('Série' in posicoes)) return {};

  const manuais = {};
  valoresAba.slice(indiceCabecalho + 1).forEach(linha => {
    if (!linha || !texto_(linha[0])) return;
    const celula = nome => {
      const pos = posicoes[nome];
      if (pos === undefined || pos >= linha.length) return '';
      const valor = linha[pos];
      return valor instanceof Date ? valor : texto_(valor);
    };
    const registro = {};
    lidas.forEach(nome => { registro[nome] = celula(nome); });
    manuais[chaveNf(linha[0], celula('Série'))] = registro;
  });
  return manuais;
}

function statusEntregaInicial(linha) {
  if (linha['Série'].toUpperCase().startsWith('S')) return 'Não se aplica';
  if (STATUS_ABERTOS.indexOf(linha['Status financeiro']) >= 0) return 'A validar';
  return '';
}

function selecionarLinhas(controle, manuais, desde) {
  const limite = data_(desde);
  const aberta = l => STATUS_ABERTOS.indexOf(l['Status financeiro']) >= 0;
  const selecionado = controle.filter(l =>
    (l['Emissão'] && l['Emissão'] >= limite) || aberta(l) || chaveNf(l['NF'], l['Série']) in manuais);
  const tempo = d => (d ? d.getTime() : Infinity);
  const abertas = selecionado.filter(aberta)
    .sort((a, b) => (tempo(a['Vencimento']) - tempo(b['Vencimento'])) || (tempo(a['Emissão']) - tempo(b['Emissão'])));
  const fechadas = selecionado.filter(l => !aberta(l))
    .sort((a, b) => tempo(b['Emissão']) - tempo(a['Emissão']));
  return abertas.concat(fechadas);
}

/** O nº do local de entrega é por cadastro de cliente: o local 4 da Vibra
 * Santo André (Louis Dreyfus) não é o local 4 da Vibra BA (Ferbasa). */
function chaveLocal(cnpj, numero) {
  const digitos = texto_(cnpj).replace(/\D/g, '');
  const n = inteiro_(numero);
  return !digitos || n === null ? null : `${digitos}|${n}`;
}

/** {chave: cliente final} da aba Locais de entrega, mantida pelo time. */
function lerLocaisEntrega(valoresAba) {
  const indice = (valoresAba || []).slice(0, 10).findIndex(l => l && texto_(l[0]) === CABECALHO_LOCAIS[0]);
  if (indice < 0) return {};
  const locais = {};
  valoresAba.slice(indice + 1).forEach(linha => {
    const chave = chaveLocal(linha[0], linha[1]);
    if (chave !== null) locais[chave] = texto_(linha[2]);
  });
  return locais;
}

/** Linhas pra acrescentar na aba Locais de entrega: cada (filial, nº) que
 * aparece nas NFs e ainda não está lá, com as NFs onde apareceu. */
function locaisSemCadastro(tabela, locais) {
  const vistos = {};
  tabela.forEach(l => {
    const chave = chaveLocal(l['Filial Vibra (CNPJ)'], l['Local de entrega (nº)']);
    if (chave === null || chave in locais) return;
    if (!vistos[chave]) {
      vistos[chave] = { cnpj: l['Filial Vibra (CNPJ)'], numero: l['Local de entrega (nº)'], nfs: [], remessas: [],
                        cliente: '', codigo: '' };
    }
    const v = vistos[chave];
    v.nfs.push(l['NF']);
    if (l._clienteRemessa && !v.cliente) {
      v.cliente = l._clienteRemessa;
      v.codigo = l._codigoRemessa || '';
    }
    if (l['NF de remessa']) v.remessas.push(l['NF de remessa']);
  });
  const ordem = lista => lista.slice().sort((x, y) => (Number(x) || 0) - (Number(y) || 0)).join(', ');
  return Object.keys(vistos)
    .map(chave => vistos[chave])
    .sort((a, b) => (a.cnpj.replace(/\D/g, '') < b.cnpj.replace(/\D/g, '') ? -1
      : a.cnpj.replace(/\D/g, '') > b.cnpj.replace(/\D/g, '') ? 1 : a.numero - b.numero))
    .map(v => {
      const nfs = ordem(v.nfs);
      let origem;
      if (v.cliente) origem = `Cliente da NF de remessa ${ordem(v.remessas)} (preenchido pelo extrator)`;
      else origem = v.nfs.length === 1 ? `Preencher: visto na NF ${nfs}` : `Preencher: visto nas NFs ${nfs}`;
      return [`'${v.cnpj}`, v.numero, v.cliente, v.codigo ? `'${v.codigo}` : '', origem];
    });
}

/** {chave: {coluna manual: valor}} da aba Importar operacional. */
function lerImportacao(valoresAba) {
  const indice = (valoresAba || []).slice(0, 10).findIndex(l => l && texto_(l[0]) === 'NF');
  if (indice < 0) return {};
  const cabecalho = valoresAba[indice].map(texto_);
  const serie = cabecalho.indexOf('Série');
  if (serie < 0) return {};
  const colunas = COLUNAS_MANUAIS.concat(['Nº Pedido Vibra']).filter(nome => cabecalho.indexOf(nome) >= 0);
  const importados = {};
  valoresAba.slice(indice + 1).forEach(linha => {
    if (!linha || !texto_(linha[0])) return;
    const valores = {};
    colunas.forEach(nome => {
      const valor = linha[cabecalho.indexOf(nome)];
      if (texto_(valor)) valores[nome] = valor instanceof Date ? valor : texto_(valor);
    });
    if (Object.keys(valores).length) importados[chaveNf(linha[0], texto_(linha[serie]))] = valores;
  });
  return importados;
}

function mesclarManuais(selecionado, manuais, locais, importados) {
  locais = locais || {};
  importados = importados || {};
  return selecionado.map(original => {
    const linha = Object.assign({}, original);
    COLUNAS_MANUAIS.forEach(nome => { linha[nome] = ''; });
    const anterior = manuais[chaveNf(linha['NF'], linha['Série'])];
    if (!anterior) {
      linha['Status entrega'] = statusEntregaInicial(linha);
    } else {
      COLUNAS_MANUAIS.forEach(nome => { linha[nome] = anterior[nome] === undefined ? '' : anterior[nome]; });
      const status = linha['Status entrega'];
      if (MIGRACAO_STATUS_ENTREGA[status]) linha['Status entrega'] = MIGRACAO_STATUS_ENTREGA[status];
      if (!linha['Nº Pedido Vibra'] && anterior['Nº Pedido Vibra']) {
        linha['Nº Pedido Vibra'] = anterior['Nº Pedido Vibra'];
      }
    }
    const importado = importados[chaveNf(linha['NF'], linha['Série'])] || {};
    Object.keys(importado).forEach(nome => {
      if (!texto_(linha[nome])) linha[nome] = importado[nome];
    });
    const cliente = locais[chaveLocal(linha['Filial Vibra (CNPJ)'], linha['Local de entrega (nº)'])] || '';
    linha[COLUNA_CLIENTE_FINAL] = cliente;
    const digitado = anterior ? anterior[COLUNA_CLIENTE_FINAL_DIGITADO] || '' : '';
    if (digitado && digitado !== cliente) {
      const obs = linha['Obs. Emissão'];
      linha['Obs. Emissão'] = `Cliente final: ${digitado}` + (obs ? ` | ${obs}` : '');
    }
    return linha;
  });
}

function montarTabela(linhasDw, valoresAba, desde, valoresLocais, valoresImportacao) {
  const manuais = lerPreenchimentoManual(valoresAba);
  return mesclarManuais(selecionarLinhas(montarControle(linhasDw), manuais, desde), manuais,
    lerLocaisEntrega(valoresLocais), lerImportacao(valoresImportacao));
}

/** Linhas A a S prontas pro setValues: texto que a planilha não deve
 * converter (série, pedido, CNPJ, NF de devolução) vai com apóstrofo. A
 * coluna O (cliente final) vai vazia: é fórmula e gravarControle_ pula. */
function linhasParaPlanilha(tabela) {
  const protegido = v => (v ? `'${v}` : '');
  return tabela.map(l => {
    const nf = l['NF'].replace(/\D/g, '');
    const saldo = l['Saldo em aberto (R$)'];
    return [
      nf ? Number(nf) : l['NF'],
      protegido(l['Série']),
      l['Emissão'] || '',
      l['Vencimento'] || '',
      l['Valor (R$)'],
      saldo === null || saldo === 0 ? '' : saldo,
      protegido(l['Nº Pedido Vibra']),
      protegido(l['Filial Vibra (CNPJ)']),
      l['Tipo de NF'],
      l['Material'],
      l['Status financeiro'],
      l['Data pagamento / baixa'] || '',
      protegido(l['NF de devolução']),
      l['Local de entrega (nº)'] === null ? '' : l['Local de entrega (nº)'],
      protegido(l['NF de remessa']),
      '',
    ].concat(COLUNAS_MANUAIS.map(nome => l[nome]));
  });
}
