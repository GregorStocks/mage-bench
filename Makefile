-include .env

# The target directory is used for setting where the output zip files will end up
# You can override this with an environment variable, ex
# TARGET_DIR=my_custom_directory make package
# Alternatively, you can set this variable in the .env file
TARGET_DIR ?= deploy/
WEBSITE_NPM_STAMP := website/node_modules/.install-stamp
GOLDEN_N ?= 2

# make check runs the java targets in parallel, and concurrent mvn processes
# race on module target/ directories (build-cache restores delete class files
# mid-compile) and on local-repository installs. Serialize every mvn that
# make check can run concurrently.
MVN_LOCKED = mkdir -p $(CURDIR)/tmp && flock $(CURDIR)/tmp/mvn.lock mvn

.PHONY: clean
clean:
	mvn clean

.PHONY: lint
lint:
	issue-lint
	uv run python -m magebench.cli.checks.lint_scripts_are_python
	uv run python -m magebench.cli.checks.lint_no_fallback
	uv run ruff check --config ruff-lint.toml src/ tests/

.PHONY: lint-java
lint-java:
	$(MVN_LOCKED) -q -pl Mage.Client.Bridge -am -DskipTests -Pjava-lint verify
	$(MAKE) verify-mcp-tools

.PHONY: lint-fix
lint-fix:
	uv run ruff check --config ruff-lint.toml --fix src/ tests/

.PHONY: format
format:
	uv run ruff format src/ tests/

.PHONY: lint-md
lint-md: $(WEBSITE_NPM_STAMP)
	website/node_modules/.bin/markdownlint-cli2

.PHONY: lint-website
lint-website: $(WEBSITE_NPM_STAMP)
	cd website && npm run lint

.PHONY: astro-check
astro-check: $(WEBSITE_NPM_STAMP)
	cd website && npm run check

.PHONY: format-check
format-check:
	uv run ruff format --check src/ tests/

.PHONY: typecheck
typecheck:
	uv run mypy src/magebench/

.PHONY: test
test:
	uv run pytest tests/ -n auto --dist=load

# Run Java unit tests for mage-bench's own modules. Upstream xmage module
# tests (Mage, Mage.Client, ...) are trusted to run in the upstream repo.
# Usage: make test-java [PL=Mage.Client.Bridge] [TEST=SomeTestClass]
TEST_JAVA_MODULES := Mage.Server,Mage.Client.Bridge,Mage.Client.Observer
PL ?= $(TEST_JAVA_MODULES)
.PHONY: test-java
test-java:
	$(MVN_LOCKED) -q -pl $(PL) -am -DskipTests -T 1C install
	$(MVN_LOCKED) -q test -pl $(PL) $(if $(TEST),-Dtest="$(TEST)",)

.PHONY: test-js
test-js: $(WEBSITE_NPM_STAMP)
	cd website && npx vitest run

.PHONY: test-e2e
test-e2e: $(WEBSITE_NPM_STAMP)
	cd website && npm run build && npx vitest run --config vitest.e2e.config.js

.PHONY: check
check:
	@uv run python -m magebench.cli.checks.quiet_check $(if $(VERBOSE),-v)
	@touch tmp/.check-passed

.PHONY: test-golden
test-golden:
	GOLDEN_INTEGRATION=1 uv run pytest -m golden -v tests/ $(if $(GOLDEN_N),-n $(GOLDEN_N) --dist=load,) $(if $(K),-k "$(K)")

.PHONY: regen-golden
regen-golden:
	GOLDEN_INTEGRATION=1 UPDATE_GOLDEN=1 uv run pytest -m golden -v tests/ $(if $(K),-k "$(K)")
	UPDATE_BLUNDER_GOLDEN=1 uv run pytest tests/test_blunder_golden_prompts.py -v

.PHONY: build
build:
	mvn install package -DskipTests

.PHONY: package
package:
	# Packaging Mage.Client to zip
	cd Mage.Client && mvn package assembly:single
	# Packaging Mage.Server to zip
	cd Mage.Server && mvn package assembly:single
	# Copying the files to the target directory
	mkdir -p $(TARGET_DIR)
	cp ./Mage.Server/target/mage-server.zip $(TARGET_DIR)
	cp ./Mage.Client/target/mage-client.zip $(TARGET_DIR)

# Note that the proper install script is located under ./Utils/build-and-package.pl
# and that should be used instead. This script is purely for convenience.
# The perl script bundles the artifacts into a single zip
.PHONY: install
install: clean build package

# Regenerate leaderboard + Elo data from game results
.PHONY: leaderboard
leaderboard:
	@uv run python -m magebench.cli.generate_leaderboard

# Build the website (Astro static site).
# Only rebuilds when dist/ is missing; delete dist/ to force a rebuild.
.PHONY: build-website
build-website: leaderboard $(WEBSITE_NPM_STAMP)
	@if [ ! -d website/dist ]; then echo "Building website..."; cd website && npx astro build; fi

# Run a game. CONFIG selects a config from configs/ (or a path to a custom file).
# Default: 2 CPU Jumpstart duel, no API keys needed.
#   make run                              # free, no API keys (2 CPU Jumpstart duel)
#   make run CONFIG=round-robin-commander  # 4 LLM pilots (needs OPENROUTER_API_KEY)
#   make run CONFIG=path/to/x.json  # custom config file
# Pass OUTPUT to specify recording path: make run OUTPUT=/path/to/video.mov
# Parallel games: make run CONFIG=round-robin-commander GAMES=3
CONFIG ?= jumpstart-dumb
.PHONY: run
run:
	@CONFIG_PATH="$(CONFIG)"; \
	case "$$CONFIG_PATH" in \
	  */*|*.json) ;; \
	  *) CONFIG_PATH="configs/$$CONFIG_PATH.json" ;; \
	esac; \
	uv run python -m magebench.cli --observer \
	  --record$(if $(OUTPUT),=$(OUTPUT)) $(if $(GAMES),--games $(GAMES)) \
	  --config "$$CONFIG_PATH" $(ARGS)

# List available configs
.PHONY: list-configs
list-configs:
	@for f in configs/*.json; do printf "  %s\n" "$$(basename $$f .json)"; done

# Generate mcp-tools.json5 with MCP tool definitions
# Compiles first to pick up any Java source changes.
.PHONY: regen-mcp-tools
regen-mcp-tools:
	mvn -q -pl Mage.Client.Bridge -am -DskipTests -Dmaven.build.cache.enabled=false install
	cd Mage.Client.Bridge && mvn -q exec:exec -Dexec.executable=java '-Dexec.args=-cp %classpath mage.client.bridge.McpServer' \
		| PYTHONPATH=../src:.. uv run --project .. python -m magebench.cli.mcp_tools_json5 > ../website/src/data/mcp-tools.json5

# Launch the desktop client (for image downloads, deck building, etc.)
.PHONY: run-client
run-client:
	cd Mage.Client && mvn -q exec:java

# Run the website dev server (port is set per-worktree in .env by worktree_setup.py)
WEBSITE_PORT ?= 4321
.PHONY: website
website: leaderboard $(WEBSITE_NPM_STAMP)
	@HOSTNAME=$$(python3 -c "import json; print(json.load(open('$(HOME)/.mage-bench/config.json'))['hostname'])"); \
	echo "  http://$$HOSTNAME:$(WEBSITE_PORT)/"; \
	echo ""
	cd website && npx astro dev --host --port $(WEBSITE_PORT)

$(WEBSITE_NPM_STAMP): website/package.json website/package-lock.json
	@mkdir -p tmp website/node_modules
	@flock tmp/website-npm-install.lock sh -c '\
		cd website && \
		if [ ! -f node_modules/.install-stamp ] || find package.json package-lock.json -newer node_modules/.install-stamp | grep -q .; then \
			npm ci --prefer-offline --no-audit --no-fund; \
			touch node_modules/.install-stamp; \
		fi'

# Render D2 architecture diagram to SVG for the website
.PHONY: diagrams
diagrams:
	~/.local/bin/d2 --layout elk --theme 200 --pad 60 doc/architecture.d2 website/public/architecture.svg

# Export a game log for the website visualizer
# Usage: make export-game GAME=game_20260208_220934
.PHONY: export-game
export-game:
	uv run python -m magebench.cli.export_game $(GAME)

# Upload a game recording to YouTube
# Usage: make upload-youtube GAME=game_20260208_220934
.PHONY: upload-youtube
upload-youtube:
	uv run python -m magebench.cli.upload_youtube $(GAME)

# Extract a screenshot from a game recording
# Usage: make screenshot [GAME=path] [T=time] [FILE=path]
#   T=-0.5  (default) 0.5s before end. Negative = from end, positive = from start.
#   GAME    path to game log dir (default: most recent)
#   FILE    output path (default: screenshot.png inside game dir)
.PHONY: screenshot
screenshot:
	@GAME_DIR=$${GAME:-$$(ls -1td ~/.mage-bench/logs/game_* 2>/dev/null | head -1)}; \
	if [ -z "$$GAME_DIR" ]; then echo "No game logs found in ~/.mage-bench/logs/" >&2; exit 1; fi; \
	VIDEO="$$GAME_DIR/recording.mov"; \
	if [ ! -f "$$VIDEO" ]; then echo "No recording.mov in $$GAME_DIR" >&2; exit 1; fi; \
	OUT=$${FILE:-$$GAME_DIR/screenshot.png}; \
	TIME=$${T:--0.5}; \
	if echo "$$TIME" | grep -q '^-'; then \
	  ffmpeg -y -sseof "$$TIME" -i "$$VIDEO" -frames:v 1 -update 1 "$$OUT" 2>/dev/null; \
	else \
	  ffmpeg -y -ss "$$TIME" -i "$$VIDEO" -frames:v 1 -update 1 "$$OUT" 2>/dev/null; \
	fi && \
	echo "Screenshot saved to $$OUT (T=$$TIME from $$VIDEO)"

# Validate sample decks against the real card database (requires make build first)
.PHONY: verify-decks
verify-decks:
	$(MVN_LOCKED) test -pl Mage.Verify -Dtest="VerifyCardDataTest#test_checkSampleDecks"

# Analyze a game for blunders using Opus 4.6 via OpenRouter
# Usage: make blunders GAME=game_20260214_185313_g1
#        make blunders GAME=website/public/games/game_20260214_185313_g1.json.gz
.PHONY: blunders
blunders:
	uv run python -m magebench.cli.analysis.blunder_analysis $(GAME)

# Blunder eval harness
.PHONY: blunder-seed
blunder-seed:
	uv run python -m magebench.cli.analysis.blunder_seed

.PHONY: blunder-audit
blunder-audit:
	uv run python -m magebench.cli.analysis.blunder_audit $(ARGS)

AUDIT_API_PORT ?= $(shell expr $(WEBSITE_PORT) + 100)
AUDIT_BIND_HOST ?= 0.0.0.0

.PHONY: blunder-audit-web
blunder-audit-web: leaderboard
	@echo "  Audit API bind: $(AUDIT_BIND_HOST):$(AUDIT_API_PORT)"
	@uv run python -m magebench.cli.analysis.blunder_audit_web --port $(AUDIT_API_PORT) --bind-host $(AUDIT_BIND_HOST) $(ARGS) &
	@sleep 1
	AUDIT_API_PORT=$(AUDIT_API_PORT) $(MAKE) website

.PHONY: blunder-baseline
blunder-baseline:
	uv run python -m magebench.cli.analysis.blunder_baseline

# Generate TypeScript types from the JSON Schema
.PHONY: regen-schema-types
regen-schema-types: $(WEBSITE_NPM_STAMP)
	cd website && npx json2ts -i ../src/magebench/game/game-export-v9.schema.json -o src/types/game-export.d.ts

# Verify generated TypeScript types are up to date
.PHONY: verify-schema-types
verify-schema-types: $(WEBSITE_NPM_STAMP)
	@TMP_SCHEMA_TYPES=$$(mktemp); \
	trap 'rm -f "$$TMP_SCHEMA_TYPES"' EXIT; \
	cd website && npx json2ts -i ../src/magebench/game/game-export-v9.schema.json -o "$$TMP_SCHEMA_TYPES"; \
	diff -q "$$TMP_SCHEMA_TYPES" src/types/game-export.d.ts > /dev/null 2>&1 \
		|| { echo "ERROR: website/src/types/game-export.d.ts is out of date. Run 'make regen-schema-types' to regenerate."; exit 1; }

# Verify mcp-tools.json5 is up to date with McpServer.java
.PHONY: verify-mcp-tools
verify-mcp-tools:
	@$(MVN_LOCKED) -q -pl Mage.Client.Bridge -am -DskipTests -Dmaven.build.cache.enabled=false install
	@cd Mage.Client.Bridge && $(MVN_LOCKED) -q exec:exec -Dexec.executable=java '-Dexec.args=-cp %classpath mage.client.bridge.McpServer' \
		| PYTHONPATH=../src:.. uv run --project .. python -m magebench.cli.mcp_tools_json5 \
		| diff --unified - ../website/src/data/mcp-tools.json5 > /tmp/mcp-tools-diff.txt 2>&1 \
		|| (echo "ERROR: website/src/data/mcp-tools.json5 is out of date. Run 'make regen-mcp-tools' to regenerate." && head -60 /tmp/mcp-tools-diff.txt && exit 1)

.PHONY: list-games-to-analyze
list-games-to-analyze:
	uv run python -m magebench.cli.analysis.find_unanalyzed $(ARGS)

# Claim exported games for analysis.
# Usage: make claim-games TYPE=deep COUNT=1
#        make claim-games TYPE=fast GAME_IDS="game_20260301_010203"
.PHONY: claim-games
claim-games:
	@if [ -z "$(TYPE)" ]; then echo "ERROR: TYPE is required. Use TYPE=fast or TYPE=deep."; exit 2; fi
	uv run python -m magebench.cli.analysis.claim_games --type $(TYPE) $(if $(COUNT),--count $(COUNT)) $(if $(MAX_STALENESS),--max-staleness $(MAX_STALENESS)) $(GAME_IDS) $(ARGS)

.PHONY: blunder-eval
blunder-eval:
	uv run python -m magebench.cli.analysis.blunder_eval $(ARGS)

.PHONY: blunder-promote
blunder-promote:
	uv run python -m magebench.cli.analysis.blunder_promote $(ARGS)

# Conclude the current season and create a postseason tournament.
# SIZE selects how many top players qualify (typically 8 or 16).
#   make conclude-season             # top 8 (default)
#   make conclude-season SIZE=16     # top 16
SIZE ?= 8
.PHONY: conclude-season
conclude-season: leaderboard
	uv run python -m magebench.cli.conclude_season $(SIZE)

# Start the next regular season after a champion has already been crowned.
.PHONY: conclude-tournament
conclude-tournament:
	uv run python -m magebench.cli.conclude_tournament

# Run a Jumpstart snake draft for the current tournament.
# Each entrant's LLM picks two half-deck packs to form their tournament deck.
.PHONY: tournament-draft
tournament-draft:
	uv run python -m magebench.cli.tournament_draft

# Run tournament match(es). GAMES=N plays N sequential matches (default: 1).
#   make tournament-game             # play the next match
#   make tournament-game GAMES=3     # play the next 3 matches
.PHONY: tournament-game
tournament-game:
	uv run python -m magebench.cli.tournament_game $(if $(GAMES),--games $(GAMES))
