.PHONY: install dev mock test image kind-up deploy secret spike rush-on rush-off down

install:
	python -m pip install -r requirements-dev.txt

dev:            ## run locally with real keys from .env
	set -a; [ -f .env ] && . ./.env; set +a; uvicorn app.main:app --reload --port 8000

mock:           ## run fully offline (no keys needed)
	MOCK_MODE=true uvicorn app.main:app --reload --port 8000

test:
	python -m pytest -q

image:
	docker build -t milan:dev .

kind-up:
	kind create cluster --config k8s/kind-config.yaml

secret:
	kubectl apply -f k8s/00-namespace.yaml
	kubectl -n milan create secret generic milan-secrets --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -

deploy: image secret
	kind load docker-image milan:dev --name milan
	kubectl apply -f k8s/10-redis.yaml -f k8s/20-milan.yaml
	kubectl -n milan rollout restart deploy/milan
	kubectl -n milan rollout status deploy/milan --timeout=120s
	@echo "Milan is live at http://localhost:8080"

spike:
	python scripts/spike.py --url http://localhost:8080 --room demo-room --guests 200 --messages 80

rush-on:
	python ops/flagsmith_toggle.py rush_mode on

rush-off:
	python ops/flagsmith_toggle.py rush_mode off

down:
	kind delete cluster --name milan
