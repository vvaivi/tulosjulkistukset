.PHONY: build fetch transform notify run test shell

build:
	docker build --target production -t helsinki-earnings-notifier .

fetch:
	docker compose run --rm notifier fetch

transform:
	docker compose run --rm notifier transform

notify:
	docker compose run --rm notifier notify --dry-run

run:
	docker compose run --rm notifier run

test:
	docker build --target test -t helsinki-earnings-notifier:test .
	docker run --rm helsinki-earnings-notifier:test

shell:
	docker compose run --rm --entrypoint sh notifier

