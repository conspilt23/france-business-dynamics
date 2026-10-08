# Feuille de route

Le pipeline, SQL Server et le rapport Power BI sont **terminés** pour le snapshot du 1er octobre 2026. Cette feuille de route ne contient donc que des extensions possibles ; aucune n’est nécessaire pour considérer le projet portfolio comme complet.

## Évolutions analytiques possibles

1. **Reconstruire le stock d’établissements actifs à chaque fin d’année (2021–2025).**  
   Le dashboard actuel présente correctement des **flux administratifs** historiques. Une future table `fact_establishment_stock_by_year` permettrait d’analyser un véritable stock annuel.

2. **Ajouter une dimension NAF 2025 dédiée.**  
   Conserver la NAF Rev.2 et la NAF 2025 comme nomenclatures distinctes, sans conversion historique inventée, puis documenter la transition officielle.

3. **Affiner les indicateurs de réactivation.**  
   Identifier les retours vers l’état actif afin de distinguer davantage les changements administratifs d’une interprétation de « mortalité ».

## Évolutions d’industrialisation

4. **Versionner le modèle Power BI en PBIP/TMDL.**  
   Le `.pbix` final est actuellement versionné comme livrable binaire. Un format projet Power BI apporterait des diffs textuels et faciliterait la revue du modèle sémantique.

5. **Automatiser le rafraîchissement mensuel.**  
   Orchestrer `check_sources → download → transform → build_mart → quality_gate → SQL` et déclencher une alerte en cas de dérive de schéma.

6. **Ajouter un job SQL Server en CI optionnelle.**  
   La CI actuelle reste légère et n’ouvre pas de base externe ; un job séparé pourrait tester les scripts sur une instance SQL Server éphémère.

7. **Ajouter linting / couverture.**  
   `ruff`, `mypy` et un seuil de couverture pourraient compléter les 353 tests actuels si le projet évolue vers un usage plus industriel.
