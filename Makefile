.PHONY: install test lint eval eval-regressed eval-live gate baseline demo clean

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

# Default suite: recorded fixtures, deterministic, no API key required.
eval:
	python -m release_gate.cli run --extractor fixture:good --out results/run.json

# The same suite against a deliberately degraded model, to show the gate firing.
eval-regressed:
	python -m release_gate.cli run --extractor fixture:regressed --out results/run.json

# Against the live model. Costs money and needs ANTHROPIC_API_KEY.
eval-live:
	python -m release_gate.cli run --extractor claude --out results/run.json

gate:
	python -m release_gate.cli gate --run results/run.json --baseline baselines/main.json

baseline:
	python -m release_gate.cli update-baseline --run results/run.json --baseline baselines/main.json

# What CI does: evaluate, then gate.
demo: eval gate

clean:
	rm -rf results .pytest_cache .ruff_cache
