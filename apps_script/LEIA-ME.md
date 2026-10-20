# Controle Vibra: Apps Script da planilha

`ControleVibra.gs` atualiza a aba "Controle NFs Vibra" da planilha
PENDENCIAS VIBRA direto do DW, com o login de quem executa. `FiltrosVibra.gs`
são os filtros (filtro rápido na linha 1 e painel lateral). Guia de uso para o time:
[GUIA-DO-TIME.md](GUIA-DO-TIME.md). Não precisa de
service account: basta a pessoa conseguir rodar consultas no projeto
`hip-bonito-453017-m2` e ler as tabelas `bronze.cigam__empresas`,
`bronze.cigam__notas_fiscais`, `silver.lancamentos_receber` e
`silver.cadastro_materiais_cigam`.

## Instalar (uma vez)

1. Na planilha: **Extensões > Apps Script**.
2. Apague o conteúdo de `Código.gs` e cole o de `ControleVibra.gs`.
   Depois, em **Arquivos** (+) > **Script**, crie um arquivo chamado
   `FiltrosVibra` e cole o conteúdo de `FiltrosVibra.gs`.
3. Em **Configurações do projeto** (engrenagem), marque "Mostrar arquivo de
   manifesto appsscript.json"; volte ao editor e substitua o conteúdo de
   `appsscript.json` pelo deste diretório. Isso liga o serviço BigQuery.
   Outra forma: em **Serviços** (+), adicionar "BigQuery API".
4. Salve, escolha a função `atualizarControleVibra` e clique em **Executar**.
   Na primeira vez o Google pede autorização (planilha e BigQuery).
5. Recarregue a planilha: aparece o menu **Controle Vibra**.
6. **Controle Vibra > Criar filtro rápido e botão na linha 1** monta a lista
   de filtros em A1, as datas do período em E1 / F1 e o botão amarelo do
   painel em G1 (o extrator em Python monta a linha 1, mas não o botão). Se o item não aparece no menu, o
   `ControleVibra.gs` colado é anterior aos filtros: cole o atual e recarregue.

## Usar

- **Controle Vibra > Atualizar agora**: reescreve as colunas A a O a partir do
  CIGAM (O = NF de remessa da venda triangular) e preserva as colunas Q a V
  preenchidas pelo time. Numa planilha anterior à coluna "NF de remessa", a
  primeira atualização insere a coluna depois do local de entrega; numa
  anterior às colunas do operacional, insere "NF comodato Vibra" e "Data
  instalação" depois do Status entrega. A aba **Listas** é regravada a cada
  atualização com as etapas do script, e a aba **Importar operacional**, se
  tiver dados, completa o que está em branco e é limpa.
- A coluna P (**Cliente final**) é fórmula: procura a filial Vibra + o nº do
  "Local de Entrega" da aba Transporte da NF no CIGAM na aba **Locais de
  entrega**. Para corrigir ou cadastrar um cliente final, edite só lá: todas
  as NFs daquele local mudam na hora, sem precisar atualizar. Local que
  aparece numa NF e ainda não está na aba é acrescentado no fim dela com o
  cliente da NF de remessa (venda triangular); sem remessa, fica em branco e
  a NF mostra "Cadastrar na aba Locais de entrega".
- **Controle Vibra > Agendar atualização diária**: roda todo dia às 7h com o
  login de quem agendou. Se essa pessoa perder o acesso ao DW, o agendamento
  para de funcionar; outra pessoa com acesso agenda de novo.
- **Controle Vibra > Remover agendamento**.
- **Filtro rápido (célula A1)**: escolher uma opção na lista filtra a aba na
  hora, pelo gatilho `onEdit` (funciona também no app do celular). Para
  "Pagos no período", as datas ficam em E1 e F1; sem datas, usa o mês atual.
  O filtro é o filtro comum da aba, igual pra todo mundo que está com a
  planilha aberta; **Todas as NFs** tira o filtro. **Atualizar o controle da
  Vibra** (em aberto e entregues) também oculta as colunas fora de A, C, D,
  F, G, H, I, J, O, P, R, S, V e W; qualquer outra opção mostra todas de novo.
- **Controle Vibra > Baixar em Excel o que está na tela** (ou o botão no
  painel): gera um `.xlsx` com o cabeçalho, as NFs visíveis no filtro, as
  colunas visíveis e a linha de totais. O arquivo é montado no próprio script
  (um .xlsx é um zip de XMLs, `partesXlsx` + `Utilities.zip`), sem arquivo
  temporário no Drive e sem permissão nova.
- **Controle Vibra > Filtros**: painel lateral com os mesmos filtros, atalhos
  de período e o resumo (NFs, valor e saldo).
- **Botão amarelo "Filtrar NFs"** (começo da faixa verde, em G1): abre o
  mesmo painel. É imagem com script: só funciona no navegador do computador.
  Para mover sem acionar, clique com o botão direito (só seleciona) e arraste.

Abaixo da última NF o script grava a **linha de totais** (marca "TOTAL" na
coluna B): quantidade de NFs, valor, saldo, quantidade e saldo em atraso, com
SUBTOTAL, que só conta as linhas visíveis no filtro. O filtro da aba vai até a
última NF, pra linha de totais não ser escondida por ele; a cada atualização o
script move a linha, ajusta o filtro e reaplica o filtro rápido de A1.

O script só grava valores: a formatação, as listas suspensas e as fórmulas das
colunas P, W e X (linha 2) já estão na planilha. Para refazer a formatação do
zero, rode o extrator em Python (`controle_vibra.py`), que reaplica o layout.

## Erros comuns

- **"O serviço BigQuery não está ativado"** (ou, na versão anterior do script,
  "BigQuery is not defined"): o projeto do Apps Script está sem o serviço
  avançado. Em **Serviços** (+), adicionar "BigQuery API" com o identificador
  `BigQuery`, ou colar o `appsscript.json` deste diretório. Acontece quando o
  script é colado num projeto novo ou o manifesto é substituído.
- **"Não consegui consultar o DW com o seu login"**: o serviço está ligado,
  mas quem executa não tem permissão de rodar consultas no projeto ou de ler
  as tabelas listadas.

## Manutenção

O script é o espelho de `controle_vibra.py`: mesmo SQL e mesmas regras.
`tests/test_apps_script.py` roda as regras do `.gs` no Node e falha se o SQL
ou alguma célula gravada divergir da versão Python; também confere as
fórmulas e as contagens dos filtros do `FiltrosVibra.gs`. Ao mudar uma regra, mude
as duas e cole o `.gs` novo no editor do Apps Script.
