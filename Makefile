.PHONY: lock iso iso-development verify verify-fast verify-release verify-hibernate lint lint-in-dev fmt test build-test verify-alongside provision-alongside fetch-alongside
lock:
	./scripts/build-in-dev python3 scripts/package_lock.py resolve .
iso:
	./scripts/build-in-dev ./scripts/build-iso
iso-development:
	./scripts/build-in-dev ./scripts/build-iso development
verify:
	./scripts/build-in-dev python3 scripts/verify.py $(ARGS)
verify-fast:
	./scripts/build-in-dev python3 scripts/verify_fast.py $(ARGS)
verify-release:
	./scripts/build-in-dev python3 scripts/verify.py --online-repository $(ARGS)
verify-hibernate:
	./scripts/build-in-dev python3 scripts/verify_hibernate.py $(ARGS)
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
# One dev-shell entry per target; build-in-dev passes through inside it.
lint:
	./scripts/build-in-dev $(MAKE) --no-print-directory lint-in-dev
lint-in-dev:
	actionlint
	ruff check . desktop/usr/bin/arch-niri-dux-migrate desktop/usr/bin/arch-niri-dux-keyring-recover
	ruff format --check . desktop/usr/bin/arch-niri-dux-migrate desktop/usr/bin/arch-niri-dux-keyring-recover
	python3 scripts/lint_shell.py
	python3 scripts/lint_shell.py --check-format
fmt:
	./scripts/build-in-dev ruff format . desktop/usr/bin/arch-niri-dux-migrate desktop/usr/bin/arch-niri-dux-keyring-recover
	./scripts/build-in-dev python3 scripts/lint_shell.py --format
test: lint
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
verify-alongside:
	./scripts/build-in-dev python3 scripts/verify_alongside.py $(ARGS)
provision-alongside:
	./scripts/build-in-dev python3 scripts/provision_alongside.py $(ARGS)
fetch-alongside:
	./scripts/build-in-dev python3 scripts/fetch_alongside.py $(ARGS)

.PHONY: heavy-guard gc
lock iso iso-development verify verify-fast verify-release verify-hibernate verify-alongside provision-alongside fetch-alongside: | heavy-guard
heavy-guard:
	@python3 scripts/workspace.py heavy-guard
gc:
	python3 scripts/workspace.py gc $(ARGS)
