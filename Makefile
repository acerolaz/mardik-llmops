.PHONY: install up down serve proxy dashboard pilote calibrer candidats verser etat test test-unit test-integration test-acceptance eval traffic ci fixtures lint fmt clean

MODE ?= normal
VERSION ?= v2
DUREE ?= 60
RPS ?= 1
APP_URL ?= http://localhost:8000

install:            ## dépendances (uv)
	uv sync

up:                 ## app + proxy de dérive + tableau de bord + pilote (docker compose)
	docker compose up -d --build
	@echo "app : http://localhost:8000/docs — proxy : http://localhost:8080/_drift — dashboard : http://localhost:8501 — pilote : docker compose logs -f pilote"

down:
	docker compose down

serve:              ## app en local, sans docker (le proxy doit tourner : make proxy)
	uv run uvicorn app.main:app --reload --port 8000

proxy:              ## proxy de dérive en local
	uv run python -m ops.drift_proxy

dashboard:          ## tableau de bord en local (texte) — DASH=serve pour la page HTML
	uv run python -m ops.dashboard $(if $(filter serve,$(DASH)),--serve,)

pilote:             ## boucle du pilote en local : surveillance, rollback auto, promotion canary
	uv run python -m ops.deploy piloter

calibrer:           ## propose des seuils depuis la production (VERSION=vX.Y.Z) ; n'écrit rien
	uv run python -m ops.seuils calibrer --version $(VERSION)

candidats:          ## cas v2 à faible confiance capturés, en attente de versement
	uv run python -m eval.enrichir lister

verser:             ## verse un candidat relu dans le jeu d'évaluation (ID=…, CLAUSES=type1,type2)
	uv run python -m eval.enrichir verser $(ID) --clauses $(CLAUSES)

etat:               ## répartition du trafic vue par la gateway (GET /gateway/etat, APP_URL)
	@curl -sf $(APP_URL)/gateway/etat && echo

test:               ## tout (unitaires + intégration + acceptance), MOCK=on
	MOCK=on uv run pytest -q

test-unit:          ## pipeline v2, gate, versions, routage, transitions, workflows, signaux, pilotage, seuils, anonymisation : verts
	MOCK=on uv run pytest -q tests/unit

test-integration:   ## remédiation, v2, gate, publication, gateway, CLI de déploiement, surveillance, pilote, capture, enrichissement, dashboard : verts
	MOCK=on uv run pytest -q tests/integration

test-acceptance:    ## les 10 tests du brief : tous verts
	MOCK=on uv run pytest -v tests/acceptance

eval:               ## gate sur le VRAI modèle, seuils de eval/seuils.yaml (VERSION=v2, ARGS="--essais 3 --sortie eval/rapport.json")
	uv run python -m eval.run_eval --version $(VERSION) $(ARGS)

traffic:            ## trafic sur la gateway (MODE=normal|derive-score|derive-latence|erreurs)
	uv run python scripts/traffic_sim.py --mode $(MODE) --duree $(DUREE) --rps $(RPS)

fixtures:           ## (ré)enregistre les fixtures MOCK en appelant le vrai modèle
	MOCK=record uv run python -m eval.run_eval --version v1
	MOCK=record uv run python -m eval.run_eval --version $(VERSION)
	@echo "fixtures enregistrées dans eval/fixtures/ — à committer"

ci:                 ## l'équivalent local du workflow GitHub (MOCK=on) : lint, tests, gate, acceptance
	uv run ruff check .
	@if command -v actionlint >/dev/null; then actionlint .github/workflows/*.yml; \
	else echo "actionlint absent : workflows vérifiés par tests/unit/test_workflows.py seulement"; fi
	MOCK=on uv run pytest -q tests/unit
	MOCK=on uv run pytest -q tests/integration
	MOCK=on uv run python -m eval.run_eval --version v2
	MOCK=on uv run pytest -q tests/acceptance
	@echo "publication, canary : ci.yml ; promotion, rollback : promotion.yml, rollback.yml — en CI uniquement"

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

clean:
	rm -rf .pytest_cache .ruff_cache ops/metrics.jsonl eval/history.jsonl eval/.metrics_eval.jsonl eval/rapport.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
