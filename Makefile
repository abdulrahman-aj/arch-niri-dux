.PHONY: lock iso iso-development iso-checksums try verify verify-fast verify-release verify-hibernate lint lint-in-dev fmt test build-test verify-alongside provision-alongside fetch-alongside qualify help
lock:
	./scripts/in-dev-shell python3 -m tools.packages.inputs resolve . $(ARGS)
iso:
	./scripts/in-dev-shell ./tools/build/host.sh
iso-development:
	./scripts/in-dev-shell ./tools/build/host.sh development
# make try is for humans (it refuses agents itself); agents use make verify-fast ARGS=--keep.
# heavy-guard stops only agents, so humans build and try VMs on any machine.
try:
	./scripts/in-dev-shell python3 -m tools.dev.try_vm
verify:
	./scripts/in-dev-shell python3 -m tools.verification.session $(ARGS)
verify-fast:
	./scripts/in-dev-shell python3 -m tools.verification.session --fixture $(ARGS)
verify-release:
	./scripts/in-dev-shell python3 -m tools.verification.verify_release $(ARGS)
verify-hibernate:
	./scripts/in-dev-shell python3 -m tools.verification.verify_hibernate $(ARGS)
build-test:
	./scripts/in-dev-shell python3 -m unittest discover -s tests -p 'build_test.py'
# One dev-shell entry per target; in-dev-shell passes through inside it.
lint:
	./scripts/in-dev-shell $(MAKE) --no-print-directory lint-in-dev
lint-in-dev:
	python3 -m tools.dev.lint_imports
	actionlint
	ruff check . desktop/usr/bin/arch-niri-dux-keyring-recover
	ruff format --check . desktop/usr/bin/arch-niri-dux-keyring-recover
	python3 -m tools.dev.lint_shell
	python3 -m tools.dev.lint_shell --check-format
fmt:
	./scripts/in-dev-shell ruff format . desktop/usr/bin/arch-niri-dux-keyring-recover
	./scripts/in-dev-shell python3 -m tools.dev.lint_shell --format
test: lint
	./scripts/in-dev-shell python3 -m unittest discover -s tests -p '*test*.py'
verify-alongside:
	./scripts/in-dev-shell python3 -m tools.verification.verify_alongside $(ARGS)
provision-alongside:
	./scripts/in-dev-shell python3 -m tools.fixtures.provision $(ARGS)
fetch-alongside:
	./scripts/in-dev-shell python3 -m tools.fixtures.fetch $(ARGS)
qualify:
	./scripts/in-dev-shell python3 -m tools.dev.qualify $(ARGS)
help:
	@printf '%s\n' 'lock, iso, iso-development, iso-checksums, verify, verify-fast, verify-release,' \
		'verify-hibernate, verify-alongside, provision-alongside, fetch-alongside, installer-preview, gc' \
		'try: guided persistent test-drive VMs (for humans; agents use make verify-fast ARGS=--keep).'

.PHONY: heavy-guard gc
lock iso iso-development verify verify-fast verify-release verify-hibernate verify-alongside provision-alongside fetch-alongside: | heavy-guard
heavy-guard:
	@python3 -m tools.workspace heavy-guard
gc:
	python3 -m tools.workspace gc $(ARGS)

iso-checksums:
	./scripts/in-dev-shell python3 -m tools.release.iso_checksums prepare $(ARGS)

.PHONY: installer-preview
installer-preview: | heavy-guard
	./scripts/in-dev-shell python3 -m tools.dev.installer_preview $(ARGS)

.PHONY: rehearse-release
rehearse-release: | heavy-guard
	./scripts/in-dev-shell bash tools/release/rehearse.sh $(ARGS)
