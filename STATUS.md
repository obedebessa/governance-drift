# A09 — Situação editorial

- **Título:** Governance Drift: Measuring Divergence Between Approved Intent and Operational Reality in Cloud-Native Systems
- **Arquivo principal de trabalho:** `main.tex`; PDF arquivado:
  `output/pdf/governance-drift-v1.6.0.pdf`.
- **Destino editorial:** ainda não definido.
- **Estágio comprovado:** revisão científica e pacote de reprodutibilidade preparados; não submetido.
- **Repositório público:** https://github.com/obedebessa/governance-drift
- **Último arquivo permanente verificado:** v1.6.0,
  https://doi.org/10.5281/zenodo.21847543
- **DOI de conceito (todas as versões):** https://doi.org/10.5281/zenodo.21841458
- **Versão:** v1.6.0, congelada sob a tag `v1.6.0` e o DOI
  `10.5281/zenodo.21847543`;
  formaliza seleção por ativação, identidade estável, cortes temporais
  admissíveis, consistência e cobertura separadas e finalização por watermark.
  As sondas de ativação/supersessão pertencem à ablação B4 da v1.6, não à
  campanha viva primária congelada.
- **Evidência principal:** estudo fechado S1--S12; ablação B0--B4 sobre 240
  unidades pareadas; laboratório Kind/Flux/Kyverno com 180 injeções e três
  cadências (538/540 detecções, 538/538 classificações provisórias de conjuntos
  exatos condicionais sob snapshot sequencial, Hamming incondicional 0,000617);
  45/45 conjuntos compostos provisórios exatos.
- **Controles e fronteiras:** 777 polls de transições benignas sem drift de
  política, autorização, intenção ou ambiente; sete sinais de configuração e
  36 avisos epistêmicos fail-safe foram preservados. A auditoria por processos
  reais reteve 261 polls com 27/27 trajetórias exatas e 27/27 com persistência
  exata em duas sondagens. As latências medianas/P95 desde a causa foram
  4,465/9,764 s até o primeiro conjunto exato e 9,522/19,811 s até a
  persistência exata em duas sondagens; isso não equivale a finalização por
  watermark.
  Somam-se nove perfis de falha TCP, benchmark de join em memória e caminho
  vivo com os alvos de 10 e 50 Deployments concluídos (1.200/1.200 decisões por
  unidade e 240/240 decisões aninhadas exatas no agregado). A replicação
  congelada Argo CD/Gatekeeper obteve 15/15 classificações singleton projetadas
  exatas em S1/S3/S4. As medianas de primeiro alerta honesto/primeiro alerta
  substantivo/ESC foram 0,207/0,646/1,677 s, 0,157/1,659/1,659 s e
  0,215/0,215/0,650 s, respectivamente. As 15 restaurações passaram, sem
  diferenças residuais, erros de leitura da API ou resultados finais
  indecidíveis; a regra de parada não foi acionada e o cleanup foi verificado.
  Nesse segundo stack, apenas configuração e política usam superfícies nativas;
  S4 usa o adaptador compartilhado, e intenção/ambiente não são avaliados.
  Alvos que acionam regras de parada não geram amostras admitidas.
- **Limite empírico:** as execuções delimitadas demonstram realizabilidade semântica e comportamento dentro dos laboratórios; não demonstram ocorrência natural, prevalência, confiabilidade de produção, taxa universal de falso alarme ou transferência entre organizações.
- **Próxima ação editorial:** escolher o venue e adaptar apenas a camada de
  formatação, o limite de páginas e o bundle anonimizado, preservando o texto e
  a evidência congelados desta versão.
