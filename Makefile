.PHONY: all test check build clean

all: check test

test:
	@echo "Running unit tests and end-to-end verification..."
	devbox run -- python3 -m unittest discover -s tests -v

check:
	@echo "Checking Tera Term 4.02 compatibility & syntax..."
	devbox run -- python3 -m unittest -v tests/test_device_runner.py

build:
	@echo "Verifying device files and command lists..."
	@ls -la *.ttl *.csv *.txt

clean:
	@echo "Cleaning generated logs and caches..."
	rm -rf logs tests/__pycache__ __pycache__
