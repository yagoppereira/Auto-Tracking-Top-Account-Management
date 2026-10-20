# Controle de NFs da Vibra: guia do time

Planilha **PENDENCIAS VIBRA**, aba **Controle NFs Vibra**. Ela lista as NFs
emitidas para a Vibra (todas as filiais), com a situação de pagamento vinda do
CIGAM e a situação de entrega informada pela Emissão.

## De onde vêm os dados

- Um script da própria planilha consulta o CIGAM (pelo data warehouse) e
  reescreve a lista **todo dia às 7h**. Para atualizar na hora: menu
  **Controle Vibra > Atualizar agora** (só funciona pra quem tem acesso ao DW).
- O DW recebe os dados do CIGAM com até 1 dia de atraso: uma NF emitida ou
  paga hoje pode aparecer só amanhã.
- Entram as NFs emitidas desde 01/01/2026, mais as NFs antigas (desde 2024)
  que ainda estejam em aberto. NF que já estava na planilha nunca some.

## Quem preenche o quê

A linha 1 mostra, em cada bloco de colunas, quem cuida dele.

| Colunas | Bloco | Quem preenche |
|---|---|---|
| A a O | **Do CIGAM**: NF, série, emissão, vencimento, valor, saldo, nº do pedido, filial Vibra, tipo, material, status financeiro, data de pagamento, NF de devolução, nº do local de entrega, **NF de remessa** | O script. **Não edite**: tudo é reescrito na próxima atualização. Exceção: o **nº do pedido (G)** pode ser digitado quando está em branco (ver abaixo). |
| P | **Cliente final** | Fórmula. Vem da aba **Locais de entrega** (ver abaixo). |
| Q a S | **Emissão preenche**: status entrega, data da entrega / NF assinada, obs. Emissão | Emissão |
| T | **Financeiro preenche**: obs. financeiro | Financeiro |
| U e V | **Automático**: dias em atraso, próxima ação | Fórmulas |

O que o time escreve em Q a T fica preso à NF (número + série): pode reordenar
ou filtrar à vontade que, na atualização, cada informação volta pra linha
certa.

## Linha de totais

Logo abaixo da última NF fica uma linha amarela de **TOTAL**, que acompanha o
filtro escolhido (só soma as NFs que estão aparecendo):

| Coluna | Mostra |
|---|---|
| C | Quantidade de NFs |
| E | Valor total |
| F | Saldo em aberto |
| H | Quantas estão em atraso |
| J | Saldo em atraso |

Ela muda de lugar sozinha quando entram ou saem NFs. Não escreva nela.

## Status financeiro (coluna K)

Calculado pelo script a partir do título no CIGAM:

| Status | Significa |
|---|---|
| Em aberto | Título sem nenhuma baixa. |
| Pago parcial | Parte recebida; o saldo (coluna F) segue em aberto. |
| Pago | Título liquidado por recebimento. A data está na coluna L. |
| Devolvida | Baixado por abatimento e com NF de devolução da Vibra (coluna M). |
| Abatida | Baixado por abatimento, sem NF de devolução. |
| Cancelada | NF cancelada no CIGAM. |
| Baixa por perda | Baixado como perda. |
| Sem título | NF sem título a receber no DW. Costuma ser NF de ontem que o DW ainda não trouxe. Se passar de 1 dia, o Financeiro confere no CIGAM. |

## Status de entrega e próxima ação

A Emissão escolhe o **Status entrega** (coluna Q) na lista suspensa. A
**Próxima ação** (coluna V) se ajusta sozinha:

| Status entrega | Próxima ação (se a NF está em aberto) |
|---|---|
| A validar | Emissão: confirmar entrega |
| Aguardando entrega | Emissão: acompanhar entrega |
| Aguardando NF assinada | Emissão: obter NF assinada |
| Problema na entrega | Emissão: resolver entrega |
| NF assinada recebida | Financeiro: cobrar Vibra |
| Não se aplica | Financeiro: cobrar Vibra |

- NF nova e em aberto entra como **A validar**. Serviço e mensalidade entram
  como **Não se aplica**.
- NF paga, devolvida, cancelada etc. mostra **Encerrada** e fica em cinza.
- NF em aberto com o status de entrega em branco mostra a próxima ação
  "Preencher status entrega", em vermelho.
- As opções da lista ficam na aba **Listas**. Para criar uma nova, acrescente
  lá com a próxima ação correspondente.

## NF de remessa (a que o cliente final assina)

Nas vendas triangulares saem duas NFs no mesmo dia: a **NF da Vibra** (venda à
ordem, CFOP 6119/6118), que só fatura, e a **NF de remessa** (remessa por conta
e ordem, CFOP 6923/5923), que vai com o material para o cliente final. É a NF
de remessa que o cliente final recebe e assina: a coluna **O** mostra o número
dela, vindo do CIGAM (ex.: NF Vibra 25263, remessa 25265 para a Ferbasa
Pojuca). Mensalidade, serviço e venda direta não têm remessa: a coluna fica em
branco.

## Cliente final e aba "Locais de entrega"

Nas vendas triangulares, a NF sai para a Vibra mas o material vai para outro
cliente. No CIGAM, isso fica na aba Transporte da NF, no campo **Local de
Entrega**: um número que aponta para a lista de endereços de entrega da filial
da Vibra. A coluna N mostra esse número.

A aba **Locais de entrega** traduz o número para o cliente, por filial: o local
4 da Vibra Santo André é diferente do local 4 da Vibra BA.

- Para corrigir ou cadastrar um cliente final, edite **só a aba Locais de
  entrega**. Todas as NFs daquele local mudam na hora.
- Quando aparece um local novo numa NF, o script acrescenta uma linha no fim
  da aba Locais **já com o cliente da NF de remessa** (nome, cidade e código
  do cadastro). Só fica em branco quando a NF não tem remessa; aí a NF mostra
  **"Cadastrar na aba Locais de entrega"**. Para descobrir o cliente, abra a NF
  no CIGAM, aba Transporte, campo Local de Entrega.
- NF sem local de entrega (serviço, mensalidade, venda direta) fica com o
  cliente final em branco.

## Filtros

**Filtro rápido:** a célula **A1** (canto superior esquerdo, fundo branco,
escrito "Todas as NFs") é uma lista. Escolha uma opção e a aba é filtrada na
hora; um aviso no canto da tela mostra quantas NFs ficaram e o valor. A linha
1 fica sempre à vista, mesmo rolando a lista, e funciona também no celular.

| Opção | Mostra |
|---|---|
| Todas as NFs | Tira o filtro e mostra todas as colunas |
| **Atualizar o controle da Vibra** | Em aberto (ou pago parcial) e entregues (NF assinada recebida ou "Não se aplica"), **só com as colunas** A (NF), C (emissão), D (vencimento), F (saldo), G (pedido), H (filial), I (tipo), J (material), O (NF de remessa), P (cliente final), T (obs. financeiro) e U (dias em atraso). As outras colunas ficam ocultas até escolher outra opção |
| Pendentes de pagamento | Todos os títulos em aberto ou pagos em parte, com ou sem entrega confirmada |
| Pendentes entregues | Em aberto ou pago parcial, com entrega concluída (NF assinada recebida) ou "Não se aplica": o que já pode ser cobrado |
| Pendentes entregues em atraso | Os de cima com vencimento já passado |
| Aguardando a Emissão | Em aberto com entrega ainda não confirmada (a validar, aguardando, problema ou em branco) |
| Pagos | Todas as NFs com status Pago |
| Pagos no período | Pagas entre as datas de **E1** ("de") e **F1** ("até"), pela data de pagamento (coluna L). Sem datas, usa o mês atual. Mudou a data, o filtro se refaz |
| Canceladas / devolvidas | Canceladas, devolvidas, abatidas e baixas por perda |

**Painel:** o botão amarelo **Filtrar NFs**, no começo da faixa verde da
linha 1, ou o menu **Controle Vibra > Filtros** abre um painel à direita com
os mesmos filtros, atalhos de período (este mês, mês passado, últimos 30 dias,
este ano) e o resumo de quantidade, valor e saldo em aberto.

**Baixar em Excel:** no painel (botão amarelo **Filtrar NFs**), o botão verde
**Baixar em Excel o que está na tela** gera um `.xlsx` só com o que está
aparecendo: as NFs do filtro, as colunas visíveis e a linha de totais (a linha
1, com o filtro rápido e o título, não vai). Também pelo menu **Controle Vibra
> Baixar em Excel o que está na tela**. Para mandar à Vibra: escolha
**Atualizar o controle da Vibra** em A1 e baixe.

Cuidados:
- O filtro (e as colunas ocultas do "Atualizar o controle da Vibra") vale
  **para todo mundo** que está com a planilha aberta. Ao terminar, volte a
  lista de A1 para **Todas as NFs**.
- Na primeira vez que alguém abre o painel (botão ou menu), o Google pede
  autorização para o script. É só aceitar (o filtro rápido de A1 não pede).
- O botão amarelo só funciona no navegador do computador. No celular, use a
  lista de A1.

## Rotina sugerida

- **Emissão**: filtro **Aguardando a Emissão**. Para cada NF, atualize o
  status de entrega (Q). Quando a NF de remessa (coluna O) assinada chegar,
  marque **NF assinada recebida** e informe a data (R). Detalhes vão em
  Obs. Emissão (S).
- **Financeiro**: filtro **Pendentes entregues em atraso** para cobrar a
  Vibra, **Pendentes de pagamento** para a visão de tudo que está em aberto e
  **Pagos no período** para conferir os recebimentos. Anotações em
  Obs. financeiro (T).

## Dúvidas comuns

- **Escrevi em uma coluna de A a O e sumiu.** Essas colunas são reescritas a
  cada atualização. Use as colunas de observação (S ou T). A exceção é o nº do
  pedido (G): digitado numa NF em que ele está em branco, ele é mantido.
- **O nº do pedido da Vibra está na NF, mas a coluna G está em branco.** O
  script só acha o pedido quando ele está escrito na descrição de algum item
  da NF (ex.: "OC: 4503816077"). O texto de descrição/dados adicionais da NF
  que aparece no CIGAM não chega ao DW, então pedido escrito só ali não é
  encontrado (ex.: NFs 25263 e 26049). Digite o número na coluna G: ele fica
  guardado nas próximas atualizações.
- **A NF foi paga ontem e continua em aberto.** O DW atualiza com até 1 dia de
  atraso. Se continuar no dia seguinte, confira a baixa no CIGAM.
- **Uma NF não aparece.** Ela entra na próxima atualização (7h) ou ao clicar em
  **Atualizar agora**, desde que já esteja no DW.
- **O "Atualizar agora" deu erro de permissão.** Só quem tem acesso ao DW
  consegue atualizar. Os demais usam a planilha normalmente: a atualização das
  7h roda com o login de quem a agendou.
