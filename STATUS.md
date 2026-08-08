# A09 — Situação editorial

- **Título:** Governance Drift: Measuring Divergence Between Approved Intent and Operational Reality in Cloud-Native Systems
- **Arquivo principal de trabalho:** `main.tex`; o PDF versionado v1.6.0 será
  gerado somente após o congelamento e a verificação final.
- **Destino editorial:** ainda não definido.
- **Estágio comprovado:** revisão científica e pacote de reprodutibilidade preparados; não submetido.
- **Repositório público:** https://github.com/obedebessa/governance-drift
- **Último arquivo permanente verificado:** v1.5.0,
  https://doi.org/10.5281/zenodo.21845707
- **DOI de conceito (todas as versões):** https://doi.org/10.5281/zenodo.21841458
- **Versão:** candidata v1.6.0, ainda sem tag e sem DOI de versão;
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
  exata em duas sondagens; isso não equivale a finalização por watermark.
  Somam-se nove perfis de falha TCP, benchmark de join em memória e caminho
  vivo com até 50 Deployments concluídos. Os números da replicação limitada
  Argo CD/Gatekeeper permanecem **CROSS_TBD** até a repetição congelada com UID
  emitido pela superfície nativa. Nesse segundo stack, apenas configuração e
  política usam superfícies nativas; S4 usa o adaptador compartilhado, e
  intenção/ambiente não são avaliados. Alvos que acionam regras de parada não
  geram amostras admitidas.
- **Limite empírico:** as execuções delimitadas demonstram realizabilidade semântica e comportamento dentro dos laboratórios; não demonstram ocorrência natural, prevalência, confiabilidade de produção, taxa universal de falso alarme ou transferência entre organizações.
- **Próxima ação editorial:** concluir a repetição cross-stack, congelar os
  resultados, passar o verificador agregado, gerar PDF/pacote/hashes e somente
  então criar tag e depósito; depois escolher o venue e adaptar formato, limite
  de páginas e bundle anonimizado.
