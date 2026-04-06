PYTHON ?= python

.PHONY: install init doctor make-short make-batch auth-youtube auth-pexels history validate-assets test test-unit test-feature test-e2e smoke

install:
	sh scripts/install_python_deps.sh

init:
	sh scripts/setup_project.sh

doctor:
	$(PYTHON) -m youtube_kanaal doctor

make-short:
	$(PYTHON) -m youtube_kanaal make-short

make-batch:
	$(PYTHON) -m youtube_kanaal make-batch --count 3

auth-youtube:
	$(PYTHON) -m youtube_kanaal auth-youtube

auth-pexels:
	$(PYTHON) -m youtube_kanaal auth-pexels

history:
	$(PYTHON) -m youtube_kanaal list-history

validate-assets:
	$(PYTHON) -m youtube_kanaal validate-assets

smoke:
	$(PYTHON) -m youtube_kanaal test-pipeline

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest tests/unit

test-feature:
	$(PYTHON) -m pytest tests/feature

test-e2e:
	$(PYTHON) -m pytest tests/e2e
