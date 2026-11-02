.PHONY: iso verify verify-hibernate verify-personal test build-test
iso:
	./scripts/build-in-dev ./scripts/build-iso
verify:
	./scripts/build-in-dev python3 scripts/verify.py $(ARGS)
verify-hibernate:
	./scripts/build-in-dev python3 scripts/verify-hibernate.py $(ARGS)
verify-personal:
	./scripts/build-in-dev python3 scripts/verify-personal.py $(ARGS)
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
test:
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
