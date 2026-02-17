.PHONY: install install-voice install-all test lint status restart logs deploy clean help

PYTHON := python3
PIP := $(PYTHON) -m pip
SYSTEMCTL := sudo systemctl

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install core dependencies
	$(PIP) install -e ".[dev]"

install-voice: ## Install with voice support
	$(PIP) install -e ".[voice]"

install-all: ## Install everything
	$(PIP) install -e ".[voice,vision,gpu,dev]"

test: ## Run test suite
	$(PYTHON) -m pytest tests/ -v

lint: ## Lint with ruff
	$(PYTHON) -m ruff check sentient/

status: ## Show status of all sentient services
	@$(SYSTEMCTL) list-units 'sentient-*' --no-pager

health: ## Check health of all HTTP services
	@echo "Memory (8001):"; curl -s http://localhost:8001/health 2>/dev/null || echo "  DOWN"
	@echo "Contemplation (8002):"; curl -s http://localhost:8002/health 2>/dev/null || echo "  DOWN"
	@echo "Perception (8003):"; curl -s http://localhost:8003/health 2>/dev/null || echo "  DOWN"
	@echo "Web Chat (3001):"; curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost:3001/ 2>/dev/null || echo "  DOWN"

restart: ## Restart all sentient services
	$(SYSTEMCTL) restart sentient-core.target

stop: ## Stop all sentient services
	$(SYSTEMCTL) stop sentient-core.target

start: ## Start all sentient services
	$(SYSTEMCTL) start sentient-core.target

logs: ## Follow logs for all sentient services
	journalctl -u 'sentient-*' -f --no-pager

deploy: ## Deploy: install + restart services
	$(PIP) install -e "."
	sudo cp systemd/*.service /etc/systemd/system/
	$(SYSTEMCTL) daemon-reload
	$(SYSTEMCTL) restart sentient-core.target
	@echo "Deployed. Run 'make status' to verify."

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
