.PHONY: lock iso iso-development iso-checksums verify verify-fast verify-release verify-hibernate lint lint-in-dev fmt test build-test verify-alongside provision-alongside fetch-alongside
lock:
	./scripts/in-dev-shell python3 scripts/package_lock.py resolve . $(ARGS)
iso:
	./scripts/in-dev-shell ./scripts/build-iso
iso-development:
	./scripts/in-dev-shell ./scripts/build-iso development
verify:
	./scripts/in-dev-shell python3 scripts/verify.py $(ARGS)
verify-fast:
	./scripts/in-dev-shell python3 scripts/verify.py --fixture $(ARGS)
verify-release:
	./scripts/in-dev-shell python3 scripts/verify_release.py $(ARGS)
verify-hibernate:
	./scripts/in-dev-shell python3 scripts/verify_hibernate.py $(ARGS)
build-test:
	./scripts/in-dev-shell python3 -m unittest discover -s scripts -p 'build_test.py'
# One dev-shell entry per target; in-dev-shell passes through inside it.
lint:
	./scripts/in-dev-shell $(MAKE) --no-print-directory lint-in-dev
lint-in-dev:
	actionlint
	ruff check . desktop/usr/bin/arch-niri-dux-keyring-recover
	ruff format --check . desktop/usr/bin/arch-niri-dux-keyring-recover
	python3 scripts/lint_shell.py
	python3 scripts/lint_shell.py --check-format
fmt:
	./scripts/in-dev-shell ruff format . desktop/usr/bin/arch-niri-dux-keyring-recover
	./scripts/in-dev-shell python3 scripts/lint_shell.py --format
test: lint
	./scripts/in-dev-shell bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
verify-alongside:
	./scripts/in-dev-shell python3 scripts/verify_alongside.py $(ARGS)
provision-alongside:
	./scripts/in-dev-shell python3 scripts/provision_alongside.py $(ARGS)
fetch-alongside:
	./scripts/in-dev-shell python3 scripts/fetch_alongside.py $(ARGS)

.PHONY: heavy-guard gc
lock iso iso-development verify verify-fast verify-release verify-hibernate verify-alongside provision-alongside fetch-alongside: | heavy-guard
heavy-guard:
	@python3 scripts/workspace.py heavy-guard
gc:
	python3 scripts/workspace.py gc $(ARGS)

iso-checksums:
	./scripts/in-dev-shell python3 scripts/iso_checksums.py prepare $(ARGS)
