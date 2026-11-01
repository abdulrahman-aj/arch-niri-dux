.PHONY: iso verify test build-test
iso:
	./scripts/build-in-dev ./scripts/build-iso
verify:
	./scripts/build-in-dev python3 scripts/verify.py
build-test:
	./scripts/build-in-dev python3 -m unittest discover -s scripts -p 'build_test.py'
test:
	./scripts/build-in-dev bash -c 'python3 -m unittest discover -s tests && python3 -m unittest discover -s scripts -p build_test.py'
