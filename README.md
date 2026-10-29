# Vai chover amanhã? — Uberaba-MG

Dashboard com a probabilidade de chuva (≥ 1 mm) para o dia seguinte em Uberaba-MG, atualizado automaticamente todos os dias.

**Como funciona:** um Random Forest treinado com a série da estação automática do INMET (A568, 2017–2026) e com as previsões do modelo global JMA GSM corrige os vieses locais da previsão numérica e devolve uma probabilidade calibrada. Como a API pública do INMET não está entregando dados recentes, as observações do dia são substituídas pelo próprio JMA, convertido para a escala da estação por mapeamento de quantis.

```
previsao-chuva-uberaba/
├── pipeline_chuva.py            # funções comuns (busca, conversão JMA→INMET, features)
├── previsao_diaria.py           # roda todo dia no GitHub Actions
├── requirements.txt
├── modelo/
│   ├── modelo_chuva_amanha.joblib    # ← você adiciona (notebook de treino)
│   └── mapeamento_jma_inmet.json     # ← você adiciona (notebook de preparação)
├── notebooks/
│   └── preparar_dashboard.ipynb      # roda uma vez no Colab
├── docs/                        # site publicado no GitHub Pages
│   ├── index.html
│   └── data/
│       ├── backtest.json             # ← você adiciona (notebook de preparação)
│       ├── previsao.json             # gerado todo dia
│       └── historico.json            # gerado todo dia
└── .github/workflows/previsao.yml    # agendamento diário (20h10, horário de Brasília)
```

## Passo a passo

### 1. Preparação (uma vez, no Colab)
Abra `notebooks/preparar_dashboard.ipynb` no Colab e envie, quando pedido:
`inmet_uberaba_diario_rf.csv`, `modelo_chuva_amanha.joblib` e `pipeline_chuva.py`.

Ao final, ele baixa dois arquivos: `mapeamento_jma_inmet.json` e `backtest.json`.
Anote a versão do scikit-learn que aparece na terceira célula.

### 2. Criar o repositório no GitHub
1. Crie um repositório **público** chamado `previsao-chuva-uberaba`.
2. Envie todos os arquivos desta pasta (*Add file → Upload files*; arraste também a pasta `.github`).
3. Coloque `modelo_chuva_amanha.joblib` e `mapeamento_jma_inmet.json` em `modelo/`, e `backtest.json` em `docs/data/`.
4. Se a versão do scikit-learn do Colab **não** for 1.6.1, corrija a linha `scikit-learn==` em `requirements.txt`.

### 3. Ativar o site e a automação
1. **Settings → Pages:** *Source* = *Deploy from a branch*, branch `main`, pasta `/docs`.
2. **Settings → Actions → General → Workflow permissions:** marque *Read and write permissions*.
3. **Actions → Previsão diária de chuva → Run workflow** para gerar a primeira previsão sem esperar o horário agendado.

O site fica em `https://thi-damasceno.github.io/previsao-chuva-uberaba/`.

## Manutenção
- **Falha na atualização:** o dashboard mostra um aviso e mantém a última previsão válida; o erro aparece na aba *Actions*.
- **Retreinar o modelo:** substitua o `.joblib` em `modelo/` e rode de novo o notebook de preparação (o mapeamento e o teste retroativo dependem do modelo).
- **Quando houver token do INMET:** basta trocar a fonte das observações do dia em `pipeline_chuva.py` — o modelo já foi treinado com dados reais da estação.

## Fontes e créditos
- Previsões: [Open-Meteo](https://open-meteo.com/) — modelo JMA GSM (dados sob licença CC BY 4.0).
- Série histórica: [INMET](https://portal.inmet.gov.br/) — estação automática A568, Uberaba-MG.
- Previsão experimental: não substitui os avisos oficiais da Defesa Civil e do INMET.

Projeto de Thiago Damasceno Silva.
