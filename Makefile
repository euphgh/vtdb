PY ?= python3
DIST := dist

.PHONY: help test build zipapp binary clean

help:
	@echo "targets:"
	@echo "  test     run the pytest suite"
	@echo "  build    build wheel and sdist with uv"
	@echo "  zipapp   build a single-file dist/vtdb.pyz"
	@echo "  binary   build a single-file executable with PyInstaller"
	@echo "  clean    remove build artifacts"

test:
	$(PY) -m pytest tests -q

build:
	uv build

zipapp:
	mkdir -p $(DIST)
	$(PY) -m zipapp src -m "vtdb.cli:main" -p "/usr/bin/env python3" -o $(DIST)/vtdb.pyz
	chmod +x $(DIST)/vtdb.pyz
	@echo "built $(DIST)/vtdb.pyz"

binary:
	uv tool run pyinstaller --onefile --name vtdb --paths src \
		--add-data "src/vtdb/schema.sql:vtdb" packaging/pyinstaller_entry.py

clean:
	rm -rf $(DIST) build .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
