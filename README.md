# Auto Tracking: Top Account Management

## Controle de NFs da Vibra

Controle das NFs emitidas para a Vibra Energia (todas as filiais, raiz de CNPJ
34.274.233), montado direto do DW (BigQuery, dados do CIGAM) na planilha
**PENDENCIAS VIBRA**: situação financeira do título (em aberto, pago,
devolvida, cancelada...), entrega validada pela Emissão, cliente final das
vendas triangulares e filtros para o Financeiro e a Emissão.

| Caminho | O que é |
|---|---|
| `apps_script/` | Script da planilha (atualiza com o login de quem executa, filtros, menu). Instalação em [`apps_script/LEIA-ME.md`](apps_script/LEIA-ME.md) |
| `apps_script/GUIA-DO-TIME.md` | Guia de uso da planilha para a Emissão e o Financeiro |
| `controle_vibra.py` | Mesmo extrator em Python, para rodar pelo terminal com service account e reformatar a planilha do zero |
| `tests/` | Testes das regras; `test_apps_script.py` roda o `.gs` no Node e confere que as duas versões gravam as mesmas células |

```bash
pip install -r requirements.txt
python3 -m pytest                       # testes (os do Apps Script precisam de Node)
python3 controle_vibra.py --credenciais sa.json --somente-ler
```
