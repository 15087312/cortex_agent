# Cortex Agent — Web UI

**Language**: [English](./README.en.md) | [简体中文](./README.md)

Web console of Cortex Agent, containing 15 feature pages (Chat/Orchestration/Settings/Dashboard/Memory/Skills/Tools/Scheduled Tasks/Outreach/Perception/Security/System/Graph/Causal Graph/Modules), connecting to the backend via WebSocket.

## How to Start

### macOS (recommended)

```bash
# One-click start (double-click or run in terminal)
./frontend/start.command
```

Or start manually:
```bash
# Make sure the backend is already running on localhost:8080
# Start the Qt desktop client
python frontend/main.py
```

### Browser access (no Qt)

```bash
python frontend/server.py
# Open http://localhost:8765
```

### Windows

Double-click `frontend/start.bat`

## Feature Pages

| Page | Function |
|------|------|
| Chat | Real-time WebSocket chat, multi-model selection |
| Dashboard | System overview |
| Modules | Module status viewer |
| Memory | Event memory CRUD |
| Causal Graph | Causal relationship visualization |
| Tools | Tool registration and invocation |
| Security | Security policies and audit logs |
| Perception | Sensor status monitoring |
| Session Monitor | Active session list |
| System | System status and configuration |
| Settings | API Key and runtime configuration |

## WebSocket Protocol

```
Connect: ws://localhost:8080/stream/ws/{session_id}
Create session: POST /api/stream/session → {session_id}

Send: {"type": "input", "content": "消息内容"}
Stop: {"type": "stop"}
