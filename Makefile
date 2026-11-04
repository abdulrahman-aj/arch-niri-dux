.PHONY: iso iso-development verify verify-hibernate test build-test verify-alongside provision-alongside fetch-alongside
iso:
	./scripts/build-in-dev ./scripts/build-iso
iso-development:
	./scripts/build-in-dev ./scripts/build-iso development
verify:
	./scripts/build-in-dev python3 scripts/verify.py $(ARGS)
verify-hibernate:
	./scripts/build-in-dev python3 scripts/verify-hibernate.py $(ARGS)
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
test:
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
verify-alongside:
	./scripts/build-in-dev python3 scripts/verify-alongside.py $(ARGS)
provision-alongside:
	./scripts/build-in-dev python3 scripts/provision-alongside.py $(ARGS)
fetch-alongside:
	./scripts/build-in-dev python3 scripts/fetch-alongside.py $(ARGS)
