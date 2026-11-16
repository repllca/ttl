.PHONY: all test check build

all: check test

test:
	@echo "Running tests..."
	devbox run -- python3 tests/test_device_runner.py

check:
	@echo "Checking TTL syntax and configuration files..."
	devbox run -- python3 -m unittest -v tests/test_device_runner.py

build:
	@echo "Verifying device files and command lists..."
	@ls -la *.ttl *.csv *.txt
