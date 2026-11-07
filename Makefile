.PHONY: iso iso-development verify verify-fast verify-release verify-hibernate lint fmt test build-test verify-alongside provision-alongside fetch-alongside
iso:
	./scripts/build-in-dev ./scripts/build-iso
iso-development:
	./scripts/build-in-dev ./scripts/build-iso development
verify:
	./scripts/build-in-dev python3 scripts/verify.py $(ARGS)
verify-fast:
	./scripts/build-in-dev python3 scripts/verify-fast.py $(ARGS)
verify-release:
	./scripts/build-in-dev python3 scripts/verify.py --online-repository $(ARGS)
verify-hibernate:
	./scripts/build-in-dev python3 scripts/verify-hibernate.py $(ARGS)
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
lint:
	./scripts/build-in-dev actionlint
	./scripts/build-in-dev ruff check . desktop/usr/bin/arch-niri-dux-migrate
	./scripts/build-in-dev ruff format --check . desktop/usr/bin/arch-niri-dux-migrate
	./scripts/build-in-dev python3 scripts/lint-shell.py
	./scripts/build-in-dev python3 scripts/lint-shell.py --check-format
fmt:
	./scripts/build-in-dev ruff format . desktop/usr/bin/arch-niri-dux-migrate
	./scripts/build-in-dev python3 scripts/lint-shell.py --format
test: lint
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
verify-alongside:
	./scripts/build-in-dev python3 scripts/verify-alongside.py $(ARGS)
provision-alongside:
	./scripts/build-in-dev python3 scripts/provision-alongside.py $(ARGS)
fetch-alongside:
	./scripts/build-in-dev python3 scripts/fetch-alongside.py $(ARGS)
