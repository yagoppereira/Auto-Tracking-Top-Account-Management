/**
 * Filtros da aba "Controle NFs Vibra" (mesmo projeto do ControleVibra.gs: as
 * constantes ABA_CONTROLE, STATUS_* etc. vêm de lá).
 *
 * - Filtro rápido na linha 1, que é congelada e fica sempre à vista: lista em
 *   A1:D1 e as datas do período em E1 / F1. Escolher uma opção filtra na hora
 *   (onEdit, funciona também no app do celular).
 * - O botão amarelo "Filtrar NFs" no começo da faixa verde (G1) e o menu
 *   Controle Vibra > Filtros abrem o painel lateral com os mesmos filtros e o
 *   resumo (quantidade de NFs, valor e saldo em aberto).
 * - Menu Controle Vibra > Criar filtro rápido na linha 1 monta a linha 1 e o
 *   botão (o extrator em Python também monta a linha 1 ao reformatar a aba,
 *   mas imagem só o Apps Script consegue pôr).
 *
 * Cada filtro é uma lista de condições (coluna + regra) que vira duas
 * coisas: a fórmula do filtro da planilha e a mesma regra em JavaScript, que
 * conta as NFs e soma os valores pro resumo. A fórmula só usa comparações,
 * + e * (nenhuma função com mais de um argumento), então não depende do
 * separador ";" ou "," da localidade da planilha.
 *
 * O filtro é o filtro comum da aba: vale pra todo mundo que está com a
 * planilha aberta.
 */

// visão "Atualizar o controle da Vibra": só as colunas que interessam pra
// atualizar o controle (A, C, D, F, G, H, I, J, O, P, R, T, V e W). Achadas pelo
// nome do cabeçalho; as outras ficam ocultas até escolher outro filtro. A
// data de instalação é só do operacional: a Vibra não precisa dela.
const COLUNAS_CONTROLE_VIBRA = [
  'NF', 'Emissão', 'Vencimento', 'Saldo em aberto (R$)', 'Nº Pedido Vibra', 'Filial Vibra (CNPJ)',
  'Tipo de NF', 'Material', 'NF de remessa', 'Cliente final', 'NF comodato Vibra',
  'Data entrega / NF assinada', 'Obs. financeiro', 'Dias em atraso',
];

// visão da Emissão ("Aguardando a Emissão"): esconde o que não ajuda a
// validar a entrega (B, C, D, E, H, K, L, M e N); o resto fica à vista
const COLUNAS_OCULTAS_EMISSAO = [
  'Série', 'Emissão', 'Vencimento', 'Valor (R$)', 'Filial Vibra (CNPJ)', 'Status financeiro',
  'Data pagamento / baixa', 'NF de devolução', 'Local de entrega (nº)',
];

const FILTROS_VIBRA = {
  controle_vibra: {
    rotulo: 'Atualizar o controle da Vibra',
    titulo: 'Atualizar o controle da Vibra (em aberto e entregues)',
    colunas: COLUNAS_CONTROLE_VIBRA,
    condicoes: () => [
      { coluna: 'Status financeiro', em: [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL] },
      { coluna: 'Status entrega', em: STATUS_ENTREGUES },
    ],
  },
  pendentes_pagamento: {
    rotulo: 'Pendentes de pagamento',
    titulo: 'Pendentes de pagamento (todos os títulos em aberto)',
    condicoes: () => [{ coluna: 'Status financeiro', em: [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL] }],
  },
  pendentes_entregues: {
    rotulo: 'Pendentes entregues',
    titulo: 'Pendentes de pagamento com material entregue (ou não se aplica)',
    condicoes: () => [
      { coluna: 'Status financeiro', em: [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL] },
      { coluna: 'Status entrega', em: STATUS_ENTREGUES },
    ],
  },
  pendentes_entregues_atraso: {
    rotulo: 'Pendentes entregues em atraso',
    titulo: 'Pendentes de pagamento entregues e vencidos',
    condicoes: () => [
      { coluna: 'Status financeiro', em: [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL] },
      { coluna: 'Status entrega', em: STATUS_ENTREGUES },
      { coluna: 'Dias em atraso', acimaDe: 0 },
    ],
  },
  aguardando_emissao: {
    rotulo: 'Aguardando a Emissão',
    titulo: 'Aguardando a Emissão (entrega, instalação ou comodato pendente)',
    ocultas: COLUNAS_OCULTAS_EMISSAO,
    // só o que ainda depende do operacional para a Vibra pagar: sai a NF já
    // paga (ou encerrada) e a que já está ok para cobrança
    condicoes: () => [
      { coluna: 'Status entrega', em: STATUS_PENDENTES_OPERACIONAL },
      { coluna: 'Status financeiro', em: [STATUS_EM_ABERTO, STATUS_PAGO_PARCIAL] },
    ],
  },
  pagos: {
    rotulo: 'Pagos',
    titulo: 'Pagos',
    condicoes: () => [{ coluna: 'Status financeiro', em: [STATUS_PAGO] }],
  },
  pagos_periodo: {
    rotulo: 'Pagos no período',
    titulo: 'Pagos no período',
    periodo: true,
    condicoes: (de, ate) => [
      { coluna: 'Status financeiro', em: [STATUS_PAGO] },
      { coluna: 'Data pagamento / baixa', de: de, ate: ate },
    ],
  },
  encerradas_sem_pagamento: {
    rotulo: 'Canceladas / devolvidas',
    titulo: 'Canceladas, devolvidas, abatidas e perdas',
    condicoes: () => [
      { coluna: 'Status financeiro', em: [STATUS_CANCELADA, STATUS_DEVOLVIDA, STATUS_ABATIDA, STATUS_PERDA] },
    ],
  },
};

// filtro rápido da linha 1 (mesmo layout de controle_vibra.py; colunas a partir de 1)
const OPCAO_TODAS = 'Todas as NFs';
const OPCOES_FILTRO_RAPIDO = [OPCAO_TODAS].concat(Object.keys(FILTROS_VIBRA).map(k => FILTROS_VIBRA[k].rotulo));
const COLUNAS_FILTRO_RAPIDO = 4;  // A a D, mescladas
const COLUNA_PERIODO_DE = 5;      // E
const COLUNA_PERIODO_ATE = 6;     // F
const COLUNA_TITULO = 7;          // G: "Do CIGAM (...)" vai de G até o cliente final


// ============================================================================
// Filtro rápido da linha 1
// ============================================================================
/** Gatilho simples: roda pra quem editar a planilha, sem autorização. Só
 * reage à lista e às datas do filtro rápido. */
function onEdit(e) {
  if (!e || !e.range) return;
  const aba = e.range.getSheet();
  if (aba.getName() !== ABA_CONTROLE || e.range.getRow() !== 1 || e.range.getColumn() > COLUNA_PERIODO_ATE) return;
  try {
    let aviso = textoResumo(aplicarFiltroRapido_(aba));
    if (texto_(aba.getRange(1, 1).getValue()) === FILTROS_VIBRA.controle_vibra.rotulo) {
      aviso += '. Para baixar em Excel: botão amarelo Filtrar NFs ou menu Controle Vibra.';
    }
    aba.getParent().toast(aviso, 'Filtro', 10);
  } catch (erro) {
    aba.getParent().toast(erro.message, 'Filtro', 8);
  }
}

function aplicarFiltroRapido_(aba) {
  const opcao = opcaoDoRotulo(texto_(aba.getRange(1, 1).getValue()));
  if (!opcao) return filtrar_(aba, null);
  let de = '', ate = '';
  if (FILTROS_VIBRA[opcao].periodo) {
    const fuso = fusoDaPlanilha_();
    const celulaDe = aba.getRange(1, COLUNA_PERIODO_DE);
    const celulaAte = aba.getRange(1, COLUNA_PERIODO_ATE);
    const iso = valor => (valor && typeof valor.getFullYear === 'function'
      ? Utilities.formatDate(valor, fuso, 'yyyy-MM-dd') : '');
    de = iso(celulaDe.getValue());
    ate = iso(celulaAte.getValue());
    if (!de || !ate) {  // sem datas: mês atual até hoje
      ate = Utilities.formatDate(new Date(), fuso, 'yyyy-MM-dd');
      de = ate.slice(0, 8) + '01';
      celulaDe.setValue(dataBr_(de));
      celulaAte.setValue(dataBr_(ate));
    }
    validarPeriodo_(de, ate);
  }
  return filtrar_(aba, opcao, de, ate);
}

/** Monta a linha 1: lista do filtro em A1:D1, datas do período em E1 / F1,
 * o título "Do CIGAM (...)" de G até o cliente final e o botão do painel em
 * G1. Rodar de novo refaz tudo (e troca o botão). */
function criarFiltroRapido() {
  const aba = abaControle_();
  const ultima = COLUNAS_DW.length + 1;  // coluna do cliente final
  const titulo = aba.getRange(1, 1, 1, ultima).getDisplayValues()[0].find(v => v.startsWith('Do CIGAM'))
    || 'Do CIGAM (extrator)';
  aba.getRange(1, 1, 1, ultima).breakApart().clearContent().clearDataValidations().clearNote();
  aba.setRowHeight(1, 30);

  aba.getRange(1, 1, 1, COLUNAS_FILTRO_RAPIDO).merge().setValue(OPCAO_TODAS)
    .setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(OPCOES_FILTRO_RAPIDO, true)
      .setAllowInvalid(false).build())
    .setBackground('#ffffff').setFontColor(VERDE_PLANILHA).setFontWeight('bold').setHorizontalAlignment('center')
    .setNote('Filtro rápido: escolha uma opção e a lista é filtrada na hora (vale pra todo mundo com a '
      + 'planilha aberta). Para Pagos no período, informe as datas nas duas células ao lado. '
      + 'Volte para Todas as NFs ao terminar.');
  [[COLUNA_PERIODO_DE, 'de'], [COLUNA_PERIODO_ATE, 'até']].forEach(([coluna, rotulo]) => {
    aba.getRange(1, coluna)
      .setDataValidation(SpreadsheetApp.newDataValidation().requireDate().setAllowInvalid(false)
        .setHelpText('Período do filtro Pagos no período (dd/mm/aaaa)').build())
      .setNumberFormat(`"${rotulo} "dd/mm/yyyy`)
      .setBackground('#ffffff').setFontColor(VERDE_PLANILHA).setFontWeight('bold').setHorizontalAlignment('center');
  });
  aba.getRange(1, COLUNA_TITULO, 1, ultima - COLUNA_TITULO + 1).merge().setValue(titulo)
    .setBackground(VERDE_PLANILHA).setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  limparCriterios_(filtroDaAba_(aba));
  colocarBotaoFiltros_(aba);
  SpreadsheetApp.getActive().toast(
    'Filtro rápido criado em A1 e botão "Filtrar NFs" no começo da faixa verde.', 'Controle Vibra', 8);
}

/** Botão amarelo "Filtrar NFs" no começo da faixa verde do título (G1), que
 * abre o painel lateral. Fica fora da lista de A1 pra não tampar o filtro.
 * Tira antes qualquer botão anterior (inclusive o que ficava em A1). */
function colocarBotaoFiltros_(aba) {
  aba.getImages().filter(i => i.getScript() === 'abrirFiltros').forEach(i => i.remove());
  const imagem = Utilities.newBlob(Utilities.base64Decode(BOTAO_FILTROS_PNG), 'image/png', 'filtrar.png');
  aba.insertImage(imagem, COLUNA_TITULO, 1, 6, 2).setWidth(114).setHeight(26).assignScript('abrirFiltros');
}

function filtroRapidoMontado_(aba) {
  return aba.getRange(1, 1).getDataValidation() !== null;
}


// ============================================================================
// Painel lateral
// ============================================================================
function abrirFiltros() {
  const html = HtmlService.createHtmlOutput(HTML_PAINEL_FILTROS).setTitle('Filtros NFs Vibra');
  SpreadsheetApp.getUi().showSidebar(html);
}

/** Aplica o filtro escolhido no painel e devolve o resumo das NFs que ficaram
 * visíveis. `de` e `ate` vêm do painel como "aaaa-mm-dd". A lista da linha 1
 * passa a mostrar o filtro escolhido. */
function aplicarFiltroVibra(opcao, de, ate) {
  const filtro = FILTROS_VIBRA[opcao];
  if (!filtro) throw new Error(`Filtro desconhecido: ${opcao}`);
  if (filtro.periodo) validarPeriodo_(de, ate);
  const aba = abaControle_();
  if (filtroRapidoMontado_(aba)) {
    aba.getRange(1, 1).setValue(filtro.rotulo);
    if (filtro.periodo) {
      aba.getRange(1, COLUNA_PERIODO_DE).setValue(dataBr_(de));
      aba.getRange(1, COLUNA_PERIODO_ATE).setValue(dataBr_(ate));
    }
  }
  return filtrar_(aba, opcao, de, ate);
}

function limparFiltroVibra() {
  const aba = abaControle_();
  if (filtroRapidoMontado_(aba)) aba.getRange(1, 1).setValue(OPCAO_TODAS);
  return filtrar_(aba, null);
}


// ============================================================================
// Aplicação do filtro na aba
// ============================================================================
/** Troca o critério do filtro da aba pelo da opção (null = sem filtro) e
 * devolve {titulo, nfs, valor, saldo} das NFs que ficaram visíveis. */
function filtrar_(aba, opcao, de, ate) {
  const filtroAba = filtroDaAba_(aba);
  const linhaCabecalho = filtroAba.getRange().getRow();
  const { colunas, linhas } = lerTabela_(aba, linhaCabecalho);
  limparCriterios_(filtroAba);
  const filtro = opcao ? FILTROS_VIBRA[opcao] : null;
  mostrarColunas_(aba, colunas, filtro && colunasVisiveis(filtro, colunas));
  if (!filtro) return Object.assign({ titulo: OPCAO_TODAS }, resumirFiltro([], colunas, linhas));

  const condicoes = filtro.condicoes(de, ate);
  const formula = formulaFiltro(condicoes, colunas, linhaCabecalho + 1);
  filtroAba.setColumnFilterCriteria(1, SpreadsheetApp.newFilterCriteria().whenFormulaSatisfied(formula).build());
  const titulo = filtro.periodo ? `${filtro.titulo}: ${dataBr_(de)} a ${dataBr_(ate)}` : filtro.titulo;
  return Object.assign({ titulo: titulo }, resumirFiltro(condicoes, colunas, linhas));
}

/** Mostra todas as colunas e, se a opção tem lista própria, oculta as demais. */
function mostrarColunas_(aba, colunas, visiveis) {
  const total = aba.getMaxColumns();
  aba.showColumns(1, total);
  if (!visiveis) return;
  colunasAOcultar(visiveis, colunas, total).forEach(([inicio, quantidade]) => aba.hideColumns(inicio, quantidade));
}

function validarPeriodo_(de, ate) {
  if (!de || !ate) throw new Error('Informe o início e o fim do período.');
  if (de > ate) throw new Error('O início do período é depois do fim.');
}

function abaControle_() {
  const aba = SpreadsheetApp.getActive().getSheetByName(ABA_CONTROLE);
  if (!aba) throw new Error(`Aba "${ABA_CONTROLE}" não encontrada.`);
  return aba;
}

/** O filtro da aba (do cabeçalho até a última NF). Se alguém removeu, cria
 * de novo, deixando a linha de totais de fora. */
function filtroDaAba_(aba) {
  if (aba.getFilter()) return aba.getFilter();
  const totais = linhasDeTotal_(aba);
  const ultima = Math.max(totais.length ? totais[0] - 1 : aba.getLastRow(), PRIMEIRA_LINHA_DADOS);
  return aba.getRange(PRIMEIRA_LINHA_DADOS - 1, 1, ultima - PRIMEIRA_LINHA_DADOS + 2, aba.getLastColumn())
    .createFilter();
}

function limparCriterios_(filtroAba) {
  const faixa = filtroAba.getRange();
  for (let c = faixa.getColumn(); c < faixa.getColumn() + faixa.getNumColumns(); c++) {
    if (filtroAba.getColumnFilterCriteria(c)) filtroAba.removeColumnFilterCriteria(c);
  }
}

/** {colunas: {nome do cabeçalho: índice}, linhas: linhas com NF}. As colunas
 * são achadas pelo nome, então o filtro segue certo se o layout mudar. */
function lerTabela_(aba, linhaCabecalho) {
  const valores = aba.getRange(linhaCabecalho, 1, aba.getLastRow() - linhaCabecalho + 1, aba.getLastColumn())
    .getValues();
  const colunas = {};
  valores[0].forEach((nome, i) => { colunas[texto_(nome)] = i; });
  return { colunas: colunas, linhas: valores.slice(1).filter(l => texto_(l[0])) };
}


// ============================================================================
// Regras (sem serviços do Apps Script: testadas no Node)
// ============================================================================
/** Colunas que ficam à vista na opção: a lista `colunas` dela, ou todas as do
 * cabeçalho menos as `ocultas`; null = todas (não oculta nada). */
function colunasVisiveis(filtro, colunas) {
  if (filtro.colunas) return filtro.colunas;
  if (filtro.ocultas) return Object.keys(colunas).filter(nome => nome && filtro.ocultas.indexOf(nome) < 0);
  return null;
}

/** Faixas [primeira coluna (a partir de 1), quantidade] a ocultar pra ficarem
 * só as colunas `visiveis` (nomes do cabeçalho). Coluna sem cabeçalho também
 * fica oculta. */
function colunasAOcultar(visiveis, colunas, total) {
  const manter = new Set(visiveis.map(nome => indiceColuna_(colunas, nome) + 1));
  const faixas = [];
  for (let c = 1; c <= total; c++) {
    if (manter.has(c)) continue;
    const ultima = faixas[faixas.length - 1];
    if (ultima && ultima[0] + ultima[1] === c) ultima[1]++;
    else faixas.push([c, 1]);
  }
  return faixas;
}

function letraColuna_(indice) {
  let letra = '';
  for (let n = indice + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    letra = String.fromCharCode(65 + ((n - 1) % 26)) + letra;
  }
  return letra;
}

/** "aaaa-mm-dd" -> nº de série de data da planilha (dias desde 30/12/1899). */
function serialData_(iso) {
  const [a, m, d] = iso.split('-').map(Number);
  return Date.UTC(a, m - 1, d) / 86400000 + 25569;
}

function serialCelula_(valor) {
  if (valor && typeof valor.getFullYear === 'function') {
    return Date.UTC(valor.getFullYear(), valor.getMonth(), valor.getDate()) / 86400000 + 25569;
  }
  return null;
}

/** Opção do FILTROS_VIBRA pelo rótulo da lista da linha 1; null = todas. */
function opcaoDoRotulo(rotulo) {
  return Object.keys(FILTROS_VIBRA).find(k => FILTROS_VIBRA[k].rotulo === rotulo) || null;
}

function moedaBr_(valor) {
  const [inteiro, centavos] = Math.abs(valor).toFixed(2).split('.');
  return `${valor < 0 ? '-' : ''}R$ ${inteiro.replace(/\B(?=(\d{3})+(?!\d))/g, '.')},${centavos}`;
}

/** Texto do aviso que aparece depois do filtro rápido. */
function textoResumo(resumo) {
  return `${resumo.titulo}: ${resumo.nfs} NF(s) · valor ${moedaBr_(resumo.valor)}`
    + (resumo.saldo ? ` · saldo em aberto ${moedaBr_(resumo.saldo)}` : '');
}

function dataBr_(iso) {
  const [a, m, d] = iso.split('-');
  return `${d}/${m}/${a}`;
}

function indiceColuna_(colunas, nome) {
  if (!(nome in colunas)) throw new Error(`Coluna "${nome}" não encontrada no cabeçalho da aba ${ABA_CONTROLE}.`);
  return colunas[nome];
}

/** Fórmula personalizada do filtro, relativa à primeira linha de dados. Ex.:
 * =((($K3="Em aberto")+($K3="Pago parcial")+0>0)*(($P3="Não se aplica")+0>0)*1)>0
 * O "+0" e o "*1" não são enfeite: na planilha VERDADEIRO/FALSO é maior que
 * qualquer número (FALSO>0 dá VERDADEIRO) e nunca igual a um número (FALSO=0
 * dá FALSO). Um lógico sozinho comparado com 0 deixava passar todas as
 * linhas (o "Pagos" mostrava as devolvidas). Somar 0 / multiplicar por 1
 * converte o lógico em 1/0 antes de comparar. */
function formulaFiltro(condicoes, colunas, primeiraLinha) {
  const partes = condicoes.map(c => {
    const ref = `$${letraColuna_(indiceColuna_(colunas, c.coluna))}${primeiraLinha}`;
    const iguais = valores => valores.map(v => `(${ref}="${v}")`).join('+');
    if (c.em) return `(${iguais(c.em)}+0>0)`;
    if (c.foraDe) return `(${iguais(c.foraDe)}+0=0)`;
    if (c.acimaDe !== undefined) return `(${ref}>${c.acimaDe})`;
    if (c.de) return `((${ref}>=${serialData_(c.de)})*(${ref}<=${serialData_(c.ate)})>0)`;
    throw new Error('Condição de filtro inválida');
  });
  return partes.length ? `=(${partes.join('*')}*1)>0` : '=TRUE';
}

/** A mesma regra da fórmula, em JavaScript: a planilha compara texto sem
 * diferenciar maiúsculas, e célula vazia vale 0 em comparação com número. */
function atendeCondicoes(linha, condicoes, colunas) {
  const minusculo = v => String(v === null || v === undefined ? '' : v).trim().toLowerCase();
  return condicoes.every(c => {
    const valor = linha[indiceColuna_(colunas, c.coluna)];
    if (c.em) return c.em.some(v => minusculo(v) === minusculo(valor));
    if (c.foraDe) return !c.foraDe.some(v => minusculo(v) === minusculo(valor));
    if (c.acimaDe !== undefined) return typeof valor === 'number' && valor > c.acimaDe;
    if (c.de) {
      const serial = serialCelula_(valor);
      return serial !== null && serial >= serialData_(c.de) && serial <= serialData_(c.ate);
    }
    return false;
  });
}

function resumirFiltro(condicoes, colunas, linhas) {
  const valor = indiceColuna_(colunas, 'Valor (R$)');
  const saldo = indiceColuna_(colunas, 'Saldo em aberto (R$)');
  const numero = v => (typeof v === 'number' ? v : 0);
  const visiveis = linhas.filter(l => atendeCondicoes(l, condicoes, colunas));
  return {
    nfs: visiveis.length,
    valor: Math.round(visiveis.reduce((s, l) => s + numero(l[valor]), 0) * 100) / 100,
    saldo: Math.round(visiveis.reduce((s, l) => s + numero(l[saldo]), 0) * 100) / 100,
  };
}


// ============================================================================
// Baixar em Excel o que está na tela
// ============================================================================
// O .xlsx é montado aqui mesmo (é um zip de XMLs): sem arquivo temporário no
// Drive e sem pedir permissão nova. Vai só o cabeçalho (linha 2), as NFs que o
// filtro deixou visíveis, as colunas visíveis e a linha de totais; a linha 1
// (filtro rápido e título) fica de fora.

/** Abre uma janelinha que gera o arquivo e começa o download (menu). */
function abrirDownloadExcel() {
  const html = HtmlService.createHtmlOutput(HTML_DOWNLOAD_EXCEL).setWidth(360).setHeight(140);
  SpreadsheetApp.getUi().showModalDialog(html, 'Baixar em Excel');
}

/** {nome, base64, nfs}: o .xlsx do que está visível na aba de controle. */
function gerarExcelFiltrado() {
  const aba = abaControle_();
  const totais = linhasDeTotal_(aba);
  const cabecalho = PRIMEIRA_LINHA_DADOS - 1;
  const linhaTotal = totais.length ? totais[0] : null;
  const ultimaNf = linhaTotal ? linhaTotal - 1 : aba.getLastRow();
  const nCol = Math.min(aba.getLastColumn(), COLUNAS_PLANILHA.length);
  const valores = aba.getRange(cabecalho, 1, (linhaTotal || ultimaNf) - cabecalho + 1, nCol).getValues();

  const colunas = [];
  for (let c = 1; c <= nCol; c++) if (!aba.isColumnHiddenByUser(c)) colunas.push(c - 1);
  const linhas = [];
  for (let r = PRIMEIRA_LINHA_DADOS; r <= ultimaNf; r++) {
    const linha = valores[r - cabecalho];
    if (texto_(linha[0]) && !aba.isRowHiddenByFilter(r)) linhas.push(linha);
  }
  const total = linhaTotal ? valores[linhaTotal - cabecalho] : null;
  const larguras = colunas.map(i => aba.getColumnWidth(i + 1));

  const partes = partesXlsx(valores[0].map(texto_), linhas, total, colunas, larguras);
  const nome = `Controle Vibra ${Utilities.formatDate(new Date(), fusoDaPlanilha_(), 'yyyy-MM-dd')}.xlsx`;
  const blobs = Object.keys(partes).map(caminho => Utilities.newBlob(partes[caminho], 'application/xml', caminho));
  const zip = Utilities.zip(blobs, nome);
  return { nome: nome, base64: Utilities.base64Encode(zip.getBytes()), nfs: linhas.length };
}

const XLSX_CURRENCY = '"R$ "#,##0.00';
// estilos (índices de cellXfs em estilosXlsx_)
const XLSX_ESTILO = { padrao: 0, cabecalho: 1, moeda: 2, data: 3, quebra: 4, total: 5, totalMoeda: 6,
                      totalNfs: 7, totalNfsAtraso: 8, totalAtraso: 9 };
// formato de cada célula da linha de totais (o mesmo da planilha)
const XLSX_ESTILO_TOTAL = {
  'Emissão': XLSX_ESTILO.totalNfs, 'Valor (R$)': XLSX_ESTILO.totalMoeda,
  'Saldo em aberto (R$)': XLSX_ESTILO.totalMoeda, 'Filial Vibra (CNPJ)': XLSX_ESTILO.totalNfsAtraso,
  'Material': XLSX_ESTILO.totalAtraso,
};
const XLSX_COLUNAS_MOEDA = ['Valor (R$)', 'Saldo em aberto (R$)'];
const XLSX_COLUNAS_QUEBRA = ['Material', 'Cliente final', 'Obs. Emissão', 'Obs. financeiro'];

/** Arquivos do .xlsx ({caminho no zip: XML}). `cabecalho` e cada linha têm
 * todas as colunas da aba; `colunas` são os índices que entram, `larguras`
 * as larguras delas em pixels. */
function partesXlsx(cabecalho, linhas, total, colunas, larguras) {
  const ref = (c, r) => `${letraColuna_(c)}${r}`;
  const celula = (c, r, valor, estilo) => {
    if (valor === null || valor === undefined || valor === '') {
      return estilo ? `<c r="${ref(c, r)}" s="${estilo}"/>` : '';
    }
    const serial = serialCelula_(valor);
    if (serial !== null) return `<c r="${ref(c, r)}" s="${XLSX_ESTILO.data}"><v>${serial}</v></c>`;
    if (typeof valor === 'number') return `<c r="${ref(c, r)}" s="${estilo || 0}"><v>${valor}</v></c>`;
    return `<c r="${ref(c, r)}" t="inlineStr" s="${estilo || 0}"><is><t xml:space="preserve">`
      + `${xmlEscapar_(String(valor))}</t></is></c>`;
  };
  const estiloDados = nome => (XLSX_COLUNAS_MOEDA.indexOf(nome) >= 0 ? XLSX_ESTILO.moeda
    : XLSX_COLUNAS_QUEBRA.indexOf(nome) >= 0 ? XLSX_ESTILO.quebra : XLSX_ESTILO.padrao);

  const xmlLinhas = [];
  xmlLinhas.push(`<row r="1">${colunas.map((i, c) => celula(c, 1, cabecalho[i], XLSX_ESTILO.cabecalho)).join('')}</row>`);
  linhas.forEach((linha, n) => {
    xmlLinhas.push(`<row r="${n + 2}">`
      + colunas.map((i, c) => celula(c, n + 2, linha[i], estiloDados(cabecalho[i]))).join('') + '</row>');
  });
  if (total) {
    const r = linhas.length + 2;
    const celulas = colunas.map((i, c) => {
      const valor = c === 0 && !texto_(total[i]) ? MARCA_LINHA_TOTAL : total[i];
      return celula(c, r, valor, XLSX_ESTILO_TOTAL[cabecalho[i]] || XLSX_ESTILO.total);
    });
    xmlLinhas.push(`<row r="${r}">${celulas.join('')}</row>`);
  }
  const cols = colunas.map((i, c) =>
    `<col min="${c + 1}" max="${c + 1}" width="${Math.max(6, Math.round((larguras[c] || 100) / 7))}" customWidth="1"/>`);

  const ns = 'http://schemas.openxmlformats.org';
  const cabecalhoXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
  return {
    '[Content_Types].xml': cabecalhoXml
      + `<Types xmlns="${ns}/package/2006/content-types">`
      + `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`
      + '<Default Extension="xml" ContentType="application/xml"/>'
      + '<Override PartName="/xl/workbook.xml" '
      + 'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
      + '<Override PartName="/xl/worksheets/sheet1.xml" '
      + 'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
      + '<Override PartName="/xl/styles.xml" '
      + 'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
      + '</Types>',
    '_rels/.rels': cabecalhoXml
      + `<Relationships xmlns="${ns}/package/2006/relationships">`
      + `<Relationship Id="rId1" Type="${ns}/officeDocument/2006/relationships/officeDocument" `
      + 'Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml': cabecalhoXml
      + `<workbook xmlns="${ns}/spreadsheetml/2006/main" xmlns:r="${ns}/officeDocument/2006/relationships">`
      + '<sheets><sheet name="Controle Vibra" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels': cabecalhoXml
      + `<Relationships xmlns="${ns}/package/2006/relationships">`
      + `<Relationship Id="rId1" Type="${ns}/officeDocument/2006/relationships/worksheet" `
      + 'Target="worksheets/sheet1.xml"/>'
      + `<Relationship Id="rId2" Type="${ns}/officeDocument/2006/relationships/styles" Target="styles.xml"/>`
      + '</Relationships>',
    'xl/styles.xml': cabecalhoXml + estilosXlsx_(),
    'xl/worksheets/sheet1.xml': cabecalhoXml
      + `<worksheet xmlns="${ns}/spreadsheetml/2006/main">`
      + '<sheetViews><sheetView workbookViewId="0">'
      + '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
      + `<cols>${cols.join('')}</cols>`
      + `<sheetData>${xmlLinhas.join('')}</sheetData></worksheet>`,
  };
}

function estilosXlsx_() {
  const q = texto => texto.replace(/"/g, '&quot;');
  const formatos = [
    [164, XLSX_CURRENCY], [165, 'dd/mm/yyyy'], [166, '0" NF(s)"'], [167, '0" NF(s) em atraso"'],
    [168, '"Em atraso: R$ "#,##0.00'],
  ];
  const xf = (numFmt, fonte, fundo, extra) =>
    `<xf numFmtId="${numFmt}" fontId="${fonte}" fillId="${fundo}" borderId="0" xfId="0"`
    + `${numFmt ? ' applyNumberFormat="1"' : ''}${fonte ? ' applyFont="1"' : ''}${fundo ? ' applyFill="1"' : ''}`
    + (extra ? ` applyAlignment="1">${extra}</xf>` : '/>');
  const quebra = '<alignment wrapText="1" vertical="top"/>';
  return '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    + `<numFmts count="${formatos.length}">`
    + formatos.map(([id, codigo]) => `<numFmt numFmtId="${id}" formatCode="${q(codigo)}"/>`).join('')
    + '</numFmts>'
    + '<fonts count="3"><font><sz val="10"/><name val="Arial"/></font>'
    + '<font><b/><sz val="10"/><name val="Arial"/></font>'
    + '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts>'
    + '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    + '<fill><patternFill patternType="solid"><fgColor rgb="FF356854"/></patternFill></fill>'
    + '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill></fills>'
    + '<borders count="1"><border/></borders>'
    + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    + '<cellXfs count="10">'
    + xf(0, 0, 0)                                                              // padrao
    + xf(0, 2, 2, '<alignment horizontal="center" vertical="center" wrapText="1"/>')  // cabecalho
    + xf(164, 0, 0)                                                            // moeda
    + xf(165, 0, 0, '<alignment horizontal="center"/>')                        // data
    + xf(0, 0, 0, quebra)                                                      // quebra
    + xf(0, 1, 3)                                                              // total
    + xf(164, 1, 3)                                                            // totalMoeda
    + xf(166, 1, 3)                                                            // totalNfs
    + xf(167, 1, 3)                                                            // totalNfsAtraso
    + xf(168, 1, 3)                                                            // totalAtraso
    + '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    + '</styleSheet>';
}

function xmlEscapar_(texto) {
  return texto.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    // caractere de controle não é XML válido (o Excel recusa o arquivo)
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
}


// ============================================================================
// HTML do painel e imagem do botão
// ============================================================================
const HTML_PAINEL_FILTROS = `<!DOCTYPE html>
<html>
<head>
<base target="_top">
<style>
  body { font-family: Arial, sans-serif; font-size: 13px; color: #202124; margin: 12px; }
  h3 { font-size: 12px; text-transform: uppercase; color: #356854; margin: 16px 0 6px; }
  button.filtro { display: block; width: 100%; text-align: left; margin: 4px 0; padding: 8px 10px;
    border: 1px solid #dadce0; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
  button.filtro:hover { background: #e6f0ec; border-color: #356854; }
  button.filtro small { display: block; color: #5f6368; font-size: 11px; margin-top: 2px; }
  .periodo { border: 1px solid #dadce0; border-radius: 6px; padding: 8px 10px; margin: 4px 0; }
  .periodo label { display: inline-block; width: 30px; }
  .periodo input { margin: 3px 0; font-size: 13px; }
  .atalhos a { color: #356854; font-size: 11px; margin-right: 8px; cursor: pointer; text-decoration: underline; }
  button.aplicar { margin-top: 6px; padding: 6px 12px; background: #356854; color: #fff; border: 0;
    border-radius: 6px; cursor: pointer; }
  button.filtro.especial { background: #ffd666; border-color: #356854; font-weight: bold; color: #356854; }
  button.filtro.especial:hover { background: #ffe08a; }
  button.excel { display: block; width: 100%; margin: 0 0 10px; padding: 8px 10px; background: #356854;
    color: #fff; border: 0; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: bold; }
  button.limpar { margin-top: 14px; padding: 6px 12px; background: #fff; color: #356854;
    border: 1px solid #356854; border-radius: 6px; cursor: pointer; width: 100%; }
  #resultado { margin-top: 14px; padding: 10px; background: #f1f3f4; border-radius: 6px; min-height: 18px; }
  #resultado b { display: block; margin-bottom: 4px; }
  .erro { color: #c5221f; }
</style>
</head>
<body>
  <button class="excel" onclick="baixarExcel()">⬇ Baixar em Excel o que está na tela</button>
  <button class="filtro especial" onclick="aplicar('controle_vibra')">Atualizar o controle da Vibra
    <small>Em aberto com material entregue (ou não se aplica), só com as colunas A, C, D, F, G, H, I, J, O, P, R, S, T, V e W.
    Escolha outro filtro ou "Mostrar todas" para voltar as colunas</small></button>

  <h3>Financeiro</h3>
  <button class="filtro" onclick="aplicar('pendentes_pagamento')">Pendentes de pagamento
    <small>Todos os títulos em aberto ou pagos em parte</small></button>
  <button class="filtro" onclick="aplicar('pendentes_entregues')">Pendentes entregues
    <small>Em aberto com material entregue (qualquer etapa depois da entrega) ou não se aplica. A cobrança só depois do comodato assinado (coluna Próxima ação)</small></button>
  <button class="filtro" onclick="aplicar('pendentes_entregues_atraso')">Pendentes entregues em atraso
    <small>Os de cima com vencimento já passado</small></button>
  <button class="filtro" onclick="aplicar('pagos')">Pagos
    <small>Todos com status financeiro Pago</small></button>

  <div class="periodo">
    <b>Pagos no período</b> <small>(data de pagamento / baixa)</small><br>
    <label>De</label><input type="date" id="de"><br>
    <label>Até</label><input type="date" id="ate"><br>
    <div class="atalhos">
      <a onclick="periodo('mes')">Este mês</a><a onclick="periodo('mes_passado')">Mês passado</a>
      <a onclick="periodo('30')">Últimos 30 dias</a><a onclick="periodo('ano')">Este ano</a>
    </div>
    <button class="aplicar" onclick="aplicarPeriodo()">Filtrar</button>
  </div>

  <h3>Emissão</h3>
  <button class="filtro" onclick="aplicar('aguardando_emissao')">Aguardando a Emissão
    <small>Entrega, instalação ou comodato pendentes, só de NF em aberto e ainda não ok para cobrança.
    Esconde as colunas B, C, D, E, H, K, L, M e N</small></button>

  <h3>Outros</h3>
  <button class="filtro" onclick="aplicar('encerradas_sem_pagamento')">Canceladas, devolvidas e abatidas
    <small>Inclui baixas por perda</small></button>

  <button class="limpar" onclick="limpar()">Mostrar todas as NFs</button>
  <div id="resultado">Escolha um filtro.</div>

<script>
  const moeda = v => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-'
    + String(d.getDate()).padStart(2, '0');
  function mostrar(r) {
    document.getElementById('resultado').innerHTML = '<b>' + r.titulo + '</b>' + r.nfs + ' NF(s) · valor '
      + moeda(r.valor) + (r.saldo ? ' · saldo em aberto ' + moeda(r.saldo) : '');
  }
  function erro(e) {
    document.getElementById('resultado').innerHTML = '<span class="erro">' + (e.message || e) + '</span>';
  }
  function aguarde() { document.getElementById('resultado').textContent = 'Filtrando...'; }
  function aplicar(opcao, de, ate) {
    aguarde();
    google.script.run.withSuccessHandler(mostrar).withFailureHandler(erro).aplicarFiltroVibra(opcao, de || '', ate || '');
  }
  function aplicarPeriodo() {
    aplicar('pagos_periodo', document.getElementById('de').value, document.getElementById('ate').value);
  }
  function baixarExcel() {
    document.getElementById('resultado').textContent = 'Gerando o arquivo...';
    google.script.run.withSuccessHandler(r => {
      baixarArquivo(r);
      document.getElementById('resultado').innerHTML = '<b>' + r.nome + '</b>' + r.nfs + ' NF(s) no arquivo. '
        + 'Se o download não começou, <a id="baixar" href="#">clique aqui</a>.';
      document.getElementById('baixar').onclick = e => { e.preventDefault(); baixarArquivo(r); };
    }).withFailureHandler(erro).gerarExcelFiltrado();
  }
  function baixarArquivo(r) {
    const a = document.createElement('a');
    a.href = 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,' + r.base64;
    a.download = r.nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  function limpar() {
    aguarde();
    google.script.run.withSuccessHandler(mostrar).withFailureHandler(erro).limparFiltroVibra();
  }
  function periodo(tipo) {
    const hoje = new Date();
    let de, ate = hoje;
    if (tipo === 'mes') de = new Date(hoje.getFullYear(), hoje.getMonth(), 1);
    if (tipo === 'mes_passado') {
      de = new Date(hoje.getFullYear(), hoje.getMonth() - 1, 1);
      ate = new Date(hoje.getFullYear(), hoje.getMonth(), 0);
    }
    if (tipo === '30') de = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate() - 30);
    if (tipo === 'ano') de = new Date(hoje.getFullYear(), 0, 1);
    document.getElementById('de').value = iso(de);
    document.getElementById('ate').value = iso(ate);
    aplicarPeriodo();
  }
</script>
</body>
</html>`;

// botão "Filtrar NFs" (PNG 280x64, exibido em 114x26): amarelo com borda branca
// pra destacar da faixa verde da linha 1, onde fica
const BOTAO_FILTROS_PNG = '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';

// janela do menu "Baixar em Excel": gera e baixa assim que abre
const HTML_DOWNLOAD_EXCEL = `<!DOCTYPE html>
<html><head><base target="_top">
<style>body { font-family: Arial, sans-serif; font-size: 13px; margin: 12px; } a { color: #356854; }</style>
</head><body>
<div id="msg">Gerando o arquivo com o que está na tela...</div>
<script>
  function baixar(r) {
    const a = document.createElement('a');
    a.href = 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,' + r.base64;
    a.download = r.nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  google.script.run.withSuccessHandler(r => {
    baixar(r);
    document.getElementById('msg').innerHTML = '<b>' + r.nome + '</b><br>' + r.nfs + ' NF(s). '
      + 'Se o download não começou, <a id="link" href="#">clique aqui</a>.';
    document.getElementById('link').onclick = e => { e.preventDefault(); baixar(r); };
  }).withFailureHandler(e => {
    document.getElementById('msg').textContent = e.message || e;
  }).gerarExcelFiltrado();
</script>
</body></html>`;
