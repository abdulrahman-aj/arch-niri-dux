.PHONY: iso iso-development verify verify-release verify-hibernate lint test build-test verify-alongside provision-alongside fetch-alongside
iso:
	./scripts/build-in-dev ./scripts/build-iso
iso-development:
	./scripts/build-in-dev ./scripts/build-iso development
verify:
	./scripts/build-in-dev python3 scripts/verify.py $(ARGS)
verify-release:
	./scripts/build-in-dev python3 scripts/verify.py --online-repository $(ARGS)
verify-hibernate:
	./scripts/build-in-dev python3 scripts/verify-hibernate.py $(ARGS)
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
lint:
	./scripts/build-in-dev actionlint
	./scripts/build-in-dev ruff check scripts installer tests
	./scripts/build-in-dev shellcheck scripts/build-fetch-bootstrap scripts/build-guest scripts/build-in-dev scripts/build-iso scripts/publish-integration installer/configure-target.sh desktop/usr/lib/arch-niri-dux/* desktop/etc/profile.d/*.sh
test: lint
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
verify-alongside:
	./scripts/build-in-dev python3 scripts/verify-alongside.py $(ARGS)
provision-alongside:
	./scripts/build-in-dev python3 scripts/provision-alongside.py $(ARGS)
fetch-alongside:
	./scripts/build-in-dev python3 scripts/fetch-alongside.py $(ARGS)
