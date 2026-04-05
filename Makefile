.PHONY: setup collect analyze all clean help

VENV      := .venv
PYTHON    := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
RAW_DIR   := raw
DATA_DIR  := data
REPORTS   := reports
help: ## Afficher cette aide
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: $(VENV)/bin/activate ## Creer le venv et installer les dependances

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install -q -r requirements.txt
	touch $(VENV)/bin/activate

collect: ## Collecter un snapshot depuis l'API gouv.fr et copier dans raw/
	go run carburants_collector.go
	@mkdir -p $(RAW_DIR)
	@STAMP=$$(python3 -c "import json,datetime;d=json.load(open('$(DATA_DIR)/stations_latest.json'));dt=datetime.datetime.fromisoformat(d['meta']['date_collecte']);print(dt.strftime('%Y-%m-%d_%Hh%M'))"); \
	TARGET=$(RAW_DIR)/stations_$$STAMP.json; \
	if [ -f "$$TARGET" ]; then \
		printf "$$TARGET existe deja. Ecraser ? [o/N] "; \
		read answer; \
		case "$$answer" in [oOyY]*) ;; *) echo "Abandon."; exit 0 ;; esac; \
	fi; \
	cp $(DATA_DIR)/stations_latest.json "$$TARGET"; \
	echo "Copie: $$TARGET (collecte: $$STAMP)"

analyze: setup ## Analyser les tendances et generer les rapports
	$(PYTHON) analyze_trends.py

all: collect analyze ## Tout: collecter et analyser

clean: ## Supprimer les rapports generes
	rm -rf $(REPORTS)/*.html

distclean: clean ## Supprimer aussi le venv
	rm -rf $(VENV)
