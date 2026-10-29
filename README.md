# Vai chover amanhã? — Uberaba-MG

Dashboard com a probabilidade de chuva (≥ 1 mm) para o dia seguinte em Uberaba-MG, atualizado automaticamente todos os dias.

**Como funciona:** um Random Forest treinado com a série da estação automática do INMET (A568, 2017–2026) e com as previsões do modelo global JMA GSM corrige os vieses locais da previsão numérica e devolve uma probabilidade calibrada. Todos os dias, o GitHub Actions coleta as medições da estação na página TEMPO do INMET (navegador automatizado) e as combina com a previsão do JMA para amanhã. Nos dias sem medição, o JMA cobre a lacuna.

```
previsao-chuva-uberaba/
├── pipeline_chuva.py            # funções comuns (JMA, features, combinação INMET + JMA)
├── inmet_obs.py                 # leitura e tratamento das medições do INMET (mesmo critério do treino)
├── coletar_inmet.py             # coleta diária na página TEMPO (Playwright) + importação manual
├── previsao_diaria.py           # roda todo dia no GitHub Actions, depois da coleta
├── dados/
│   ├── inmet_A568_horario.csv        # medições horárias acumuladas (UTC)
│   └── importar/                     # CSVs baixados manualmente do TEMPO entram por aqui
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
└── .github/workflows/previsao.yml    # agendamento diário (23h30, horário de Brasília)
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
- **Coleta do INMET:** cada execução gera o artefato *diagnostico-coleta-inmet* (captura de tela da página e resumo da coleta), disponível por 7 dias na própria execução da aba *Actions*.
- **Lacunas nas medições:** baixe o CSV do período na página TEMPO e coloque em `dados/importar/` (*Add file → Upload files*). Ele é importado na execução seguinte.
- **Retreinar o modelo:** substitua o `.joblib` em `modelo/` e rode de novo o notebook de preparação (o mapeamento e o teste retroativo dependem do modelo).

## Fontes e créditos
- Previsões: [Open-Meteo](https://open-meteo.com/) — modelo JMA GSM (dados sob licença CC BY 4.0).
- Série histórica: [INMET](https://portal.inmet.gov.br/) — estação automática A568, Uberaba-MG.
- Previsão experimental: não substitui os avisos oficiais da Defesa Civil e do INMET.

Projeto de Thiago Damasceno Silva.
