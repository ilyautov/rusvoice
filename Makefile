.PHONY: check lint dist clean

# 145 тестов, ~40 секунд. Гоняются и без опциональных зависимостей — тогда часть
# скипается с внятной причиной, а не падает.
check:
	python3 -m pytest tests/ -q
	python3 scripts/prepublish_check.py

lint:
	python3 -m ruff check rusvoice tests

# Пруф первого пользователя вручную: собрать, проверить метаданные, поставить
# колесо в пустое окружение и позвать CLI. То же делает CI на каждом пуше.
dist: clean
	python3 -m build
	python3 -m twine check dist/*
	python3 -m venv /tmp/rusvoice-clean
	/tmp/rusvoice-clean/bin/pip -q install dist/*.whl
	/tmp/rusvoice-clean/bin/rusvoice explain "ТЗ на MVP" || true

clean:
	rm -rf dist build *.egg-info /tmp/rusvoice-clean
