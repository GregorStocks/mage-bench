# Codebase Notes

Written by Claude Opus 4.6, 2025-02-09, after exploring the code and reading game logs.

## What This Is

An AI benchmark for Magic: The Gathering, built on top of XMage (an open-source MTG engine). LLMs pilot real Magic decks against each other and against XMage's built-in CPU ("Mad AI"). Games are played in Commander (4-player) and Legacy (2-player) formats. There's a streaming spectator that records video.

## Architecture (the short version)

**Java bridge** (`Mage.Client.Headless`): A headless XMage client that exposes game actions as MCP tools. The key file is `BridgeCallbackHandler.java` (~2400 lines). It handles:
- Auto-tapping lands for mana (so the LLM doesn't have to micromanage tapping)
- Filtering unplayable actions
- Auto-passing when there's nothing to do
- Translating XMage's callback system into clean MCP tool calls

**Puppeteer** (`puppeteer/`): Connects LLMs to the MCP server via OpenAI-compatible API (through OpenRouter). Key files:
- `pilot.py` — the LLM game loop. System prompt tells the LLM to follow: pass_priority → get_action_choices → choose_action → repeat
- `orchestrator.py` — orchestrates the full game lifecycle (server, clients, spectator, recording)

## MCP Tools Available to LLMs

`pass_priority`, `get_action_choices`, `choose_action`, `get_game_state`, `get_game_log`, `get_oracle_text`, `send_chat_message`

## Player Types

- `pilot` — LLM-controlled via MCP + OpenAI API
- `cpu` — XMage's built-in "Mad AI"
- `sleepwalker` — MCP mode without an LLM attached (for testing infra)
- `potato` — auto-passes everything (punching bag)

## Game Log Observations (Feb 2026)

Logs are in `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/`. Each game dir has a config.json, pilot logs, server log, and a recording.

**DeepSeek V3** played Transguild Promenade as its first land (requires paying {1} on ETB), had no other lands to pay, and had to sacrifice it. Then typed an apologetic emoji. Classic new player mistake.

**Claude Sonnet 4.5** piloting Eldrazi Stompy got a legitimate turn-1 Eldrazi Linebreaker off Eldrazi Temple + Lotus Petal. Good sequencing. Died to OpenRouter key limits mid-game.

**Gemini 2.5 Flash** on Sneak and Show got Sneak Attack into play but drew its protection (Force of Will x2) instead of payoffs (Emrakul, Griselbrand). Sat there passing with nothing to sneak in — correct play given the hand, but its reasoning about why was off. It said "my opponent has no cards in hand, so nothing for me to do" rather than "I have no creatures to sneak in."

All models use emojis and trash-talk freely (the system prompt encourages it). Context trimming kicks in around 120 messages.

## Things I Didn't Get To

- The website/game visualizer (`make export-game`, `make website`)
- Issue tracking system in `issues/`
- How deck selection works beyond "random" and specific .dck paths
