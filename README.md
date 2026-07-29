# Cortex

Simplified chat-only AI with memory system, based on Cortex Agent (ai_backend).

## Features

- **Memory System**: Hybrid RAG (semantic + keyword + causal graph) with deep causal reasoning
- **Multi-Provider**: OpenAI, Anthropic, DashScope auto-detection
- **WebSocket Streaming**: Real-time token-by-token response
- **Session Persistence**: SQLite-backed conversation history
- **React Frontend**: Dark-themed chat UI with markdown support

## Quick Start

### 1. Setup

```bash
cd cortex
cp .env.example .env
# Edit .env with your model API key and URL
```

### 2. Backend

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### 4. Docker

```bash
docker-compose -f docker/docker-compose.yml up --build
```

## Configuration

Key `.env` variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_API_KEY` | API key | (required) |
| `MODEL_API_URL` | API endpoint | (required) |
| `MODEL_NAME` | Model name | (required) |
| `MODEL_API_FORMAT` | `openai` / `anthropic` / `dashscope` | auto-detect |
| `MODEL_TEMPERATURE` | Temperature | 0.7 |
| `MODEL_MAX_TOKENS` | Max tokens | 4096 |
| `MEMORY_REDUCE_ENABLED` | Enable memory extraction | true |
| `PORT` | Server port | 8000 |

## Architecture

```
Backend (FastAPI)
├── API Layer (REST + WebSocket)
├── Chat Engine (single-model thinker)
├── Memory System (8 modules)
│   ├── Event Store (SQLite + FAISS)
│   ├── Event Retriever (hybrid RAG)
│   ├── Causal Graph (knowledge graph)
│   ├── Causal Tree (reasoning)
│   ├── Deep Recall (3-step pipeline)
│   └── Event Reducer (LLM extraction)
├── Model Client (OpenAI/Anthropic/DashScope)
└── Database (SQLite sessions)

Frontend (React)
├── SessionSidebar
├── ChatWindow
├── MessageList + MessageBubble
└── WebSocket streaming
```

## Project Structure

```
cortex/
├── backend/
│   ├── main.py           # FastAPI entry
│   ├── config/           # Settings + prompts
│   ├── memory/           # 8 memory modules
│   ├── chat/             # Chat engine
│   ├── api/              # REST + WS routes
│   ├── database/         # SQLite persistence
│   └── utils/            # Logger + exceptions
├── infra/
│   └── model/            # LLM client
├── frontend/             # React chat UI
├── docker/               # Docker configs
└── data/                 # Runtime data (gitignored)
```
