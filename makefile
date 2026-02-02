all: build

build:
	@echo "Building Juice Shop..."
	docker build -t juice_shop ./juice_shop
	@echo "Building Attacker..."
	docker build -t attacker_carbonyl ./attacker
	@echo "Building Snort..."
	docker build -t ids_snort ./ids_snort
	@echo "Building Suricata..."
	docker build -t ids_suricata ./ids_suricata
	@echo "Building Zeek..."
	docker build -t ids_zeek ./ids_zeek

clean:
	kathara lclean

start:
	kathara lstart

restart: clean build start