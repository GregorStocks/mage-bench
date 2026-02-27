-include .env

# The target directory is used for setting where the output zip files will end up
# You can override this with an environment variable, ex
# TARGET_DIR=my_custom_directory make deploy
# Alternatively, you can set this variable in the .env file
TARGET_DIR ?= deploy/

.PHONY: clean
clean:
	mvn clean

.PHONY: lint
lint:
	uv run python scripts/lint-issues.py
	uv run --project puppeteer ruff check puppeteer/ scripts/

.PHONY: lint-fix
lint-fix:
	uv run --project puppeteer ruff check --fix puppeteer/ scripts/

.PHONY: format
format:
	uv run --project puppeteer ruff format puppeteer/ scripts/

.PHONY: format-check
format-check:
	uv run --project puppeteer ruff format --check puppeteer/ scripts/

.PHONY: typecheck
typecheck:
	uv run --project puppeteer mypy --config-file puppeteer/pyproject.toml puppeteer/src/puppeteer/

.PHONY: test
test:
	uv run --project puppeteer pytest puppeteer/

.PHONY: test-js
test-js:
	cd website && npm install --prefer-offline --no-audit --no-fund && npx vitest run

.PHONY: check
check: lint format-check typecheck test test-js verify-decks

.PHONY: test-golden
test-golden:
	cd puppeteer && GOLDEN_INTEGRATION=1 uv run pytest -m golden -v

.PHONY: update-golden
update-golden:
	cd puppeteer && GOLDEN_INTEGRATION=1 UPDATE_GOLDEN=1 uv run pytest -m golden -v

.PHONY: update-blunder-golden
update-blunder-golden:
	UPDATE_BLUNDER_GOLDEN=1 uv run --project puppeteer pytest puppeteer/tests/test_blunder_golden_prompts.py -v

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
	@uv run --project puppeteer python scripts/generate_leaderboard.py

# Build the website (Astro static site).
# Only rebuilds when dist/ is missing; delete dist/ to force a rebuild.
.PHONY: website-build
website-build: leaderboard
	@if [ ! -d website/dist ]; then echo "Building website..."; cd website && npm install --prefer-offline --no-audit --no-fund && npx astro build; fi

# Run a game. CONFIG selects a config from configs/ (or a path to a custom file).
# Default: 4 CPU players, no API keys needed.
#   make run                              # free, no API keys (2 CPU Standard duel)
#   make run CONFIG=commander-gauntlet    # 4 random LLM pilots (needs OPENROUTER_API_KEY)
#   make run CONFIG=path/to/x.json  # custom config file
# Pass OUTPUT to specify recording path: make run OUTPUT=/path/to/video.mov
# Parallel games: make run CONFIG=commander-gauntlet GAMES=3
CONFIG ?= standard-dumb
.PHONY: run
run:
	@CONFIG_PATH="$(CONFIG)"; \
	case "$$CONFIG_PATH" in \
	  */*|*.json) ;; \
	  *) CONFIG_PATH="configs/$$CONFIG_PATH.json" ;; \
	esac; \
	uv run --project puppeteer python -m puppeteer --observer \
	  --record$(if $(OUTPUT),=$(OUTPUT)) $(if $(GAMES),--games $(GAMES)) \
	  --config "$$CONFIG_PATH" $(ARGS)

# List available configs
.PHONY: configs
configs:
	@for f in configs/*.json; do printf "  %s\n" "$$(basename $$f .json)"; done

# Generate mcp-tools.json with MCP tool definitions
# Compiles first to pick up any Java source changes.
.PHONY: mcp-tools
mcp-tools:
	cd Mage.Client.Bridge && mvn -q compile exec:exec -Dexec.executable=java '-Dexec.args=-cp %classpath mage.client.bridge.McpServer' > ../website/src/data/mcp-tools.json

# Launch the desktop client (for image downloads, deck building, etc.)
.PHONY: run-client
run-client:
	cd Mage.Client && mvn -q exec:java

# Run the website dev server (port is set per-worktree in .env by worktree-setup.py)
WEBSITE_PORT ?= 4321
.PHONY: website
website: leaderboard
	cd website && npm install && npx astro dev --port $(WEBSITE_PORT)

# Export a game log for the website visualizer
# Usage: make export-game GAME=game_20260208_220934
.PHONY: export-game
export-game:
	python3 scripts/export_game.py $(GAME)

# Upload a game recording to YouTube
# Usage: make upload-youtube GAME=game_20260208_220934
.PHONY: upload-youtube
upload-youtube:
	uv run --project puppeteer python scripts/upload_youtube.py $(GAME)

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
	mvn test -pl Mage.Verify -Dtest="VerifyCardDataTest#test_checkSampleDecks"

# Analyze a game for blunders using Opus 4.6 via OpenRouter
# Usage: make blunders GAME=game_20260214_185313_g1
#        make blunders GAME=website/public/games/game_20260214_185313_g1.json.gz
.PHONY: blunders
blunders:
	uv run --project puppeteer python scripts/analysis/blunder_analysis.py $(GAME)

# Blunder eval harness
.PHONY: blunder-seed
blunder-seed:
	uv run --project puppeteer python scripts/analysis/blunder_seed.py

.PHONY: blunder-audit
blunder-audit:
	uv run --project puppeteer python scripts/analysis/blunder_audit.py $(ARGS)

.PHONY: blunder-baseline
blunder-baseline:
	uv run --project puppeteer python scripts/analysis/blunder_baseline.py

.PHONY: blunder-eval
blunder-eval:
	uv run --project puppeteer python scripts/analysis/blunder_eval.py $(ARGS)

.PHONY: blunder-promote
blunder-promote:
	uv run --project puppeteer python scripts/analysis/blunder_promote.py $(ARGS)
