# Essence Tracking — Suivi de la penurie de carburant en France

Outil de collecte et d'analyse des ruptures de carburant dans les stations-service francaises, base sur les donnees ouvertes de [data.economie.gouv.fr](https://data.economie.gouv.fr).

## Architecture

```
essence-tracking/
├── carburants_collector.go   # Collecteur Go — recupere les donnees de l'API gouv.fr
├── analyze_trends.py         # Analyseur Python — tendances et rapports
├── requirements.txt          # Dependances Python
├── Makefile                  # Commandes raccourcies
├── raw/                      # Snapshots quotidiens (stations_YYYY-MM-DD.json)
├── data/                     # Exports bruts du collecteur (horodates)
└── reports/                  # Rapports HTML generes
```

## Prerequis

- **Go** 1.21+ (pour le collecteur)
- **Python** 3.11+ (pour l'analyseur)
- **Make** (optionnel, pour les raccourcis)

## Installation rapide

```bash
make setup
```

Cela cree un environnement virtuel Python et installe les dependances.

## Utilisation

### 1. Collecter les donnees

```bash
make collect
```

Recupere un snapshot depuis l'API gouv.fr et le copie automatiquement dans `raw/stations_YYYY-MM-DD.json`. Si un fichier existe deja pour la date du jour, une confirmation est demandee avant d'ecraser.

### 2. Analyser les tendances

```bash
make analyze
```

Produit :
- Un **rapport console** avec la situation actuelle, les tendances J-1 / 7j / 30j, le detail par carburant et par region
- Un **rapport HTML interactif** dans `reports/rapport_YYYY-MM-DD.html` avec des graphiques Plotly

### 3. Tout en une commande

```bash
make all
```

Equivalent a : collect → analyze.

## Detail des rapports

### Console

```
=================================================================
  RAPPORT PENURIE CARBURANT — FRANCE
=================================================================
  Stations totales         : 9439
  Ruptures temporaires     : 2578 (27.3%)
  Tendance J-1  : +287 stations  S'AGGRAVE
  ...
```

### HTML (`reports/rapport_YYYY-MM-DD.html`)

- KPI globaux avec tendances
- Evolution des ruptures temporaires (courbe + moyennes glissantes 7j/30j)
- Ruptures par carburant (absolu et %)
- Top 15 regions (barres horizontales)
- Heatmap regions x jours
- Evolution des prix moyens
- Tableau detaille jour par jour

Les graphiques sont interactifs (zoom, hover, export PNG). Necessite une connexion internet pour charger Plotly depuis le CDN.

## Carburants suivis

| Code   | Nom     |
|--------|---------|
| gazole | Gazole  |
| sp95   | SP95    |
| sp98   | SP98    |
| e10    | E10     |
| e85    | E85     |
| gplc   | GPLc    |

## Source des donnees

- **API** : [Prix des carburants en France — Flux instantane v2](https://data.economie.gouv.fr/explore/dataset/prix-des-carburants-en-france-flux-instantane-v2)
- **Licence** : Licence Ouverte / Open Licence
