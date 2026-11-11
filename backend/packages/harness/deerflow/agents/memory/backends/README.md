# Memory Backends

`agents/memory/backends/` contains the current pluggable Memory implementations:

- `deermem/`: default structured-fact backend;
- `noop/`: empty implementation and backend template.

## Add a Backend

1. Copy `noop/` to `backends/<name>/`.
2. Implement the `MemoryManager` contract from `agents/memory/manager.py`.
3. Export `MANAGER_CLASS` from the backend package.
4. Add external dependencies to `packages/harness/pyproject.toml`.
5. Configure the backend in root `config.yaml`:

```yaml
memory:
  manager_class: <name>
  backend_config: {}
```

Restart Gateway after changing the backend or its code because the manager is process-scoped.

## Required Contract

Every backend implements the abstract methods declared by `MemoryManager`. Method signatures, keyword-only arguments and async behavior must match the base class.

Gateway memory endpoints expect the current `MemoryResponse` shape. A backend that stores a different native shape must map it at its own boundary before returning data.

Optional capabilities are detected by method presence:

- `create_fact`
- `delete_fact`
- `update_fact`
- `reload_memory`
- `warm`

If an optional operation is absent, the Gateway returns the corresponding unsupported-operation response.

## Host Inputs

The factory supplies backend configuration through `backend_config`, including:

- `storage_path`
- `tracing_callback`
- `should_keep_hidden_message`
- backend-specific values from `config.yaml`

Use `storage_path` as the writable root. Do not import project path singletons into a portable backend.

## Shared Integration Points

| File | Role |
| --- | --- |
| `agents/memory/manager.py` | ABC, factory and backend discovery |
| `agents/middlewares/memory_middleware.py` | queues conversation updates |
| `agents/memory/summarization_hook.py` | sends summarized history to Memory |
| `agents/lead_agent/prompt.py` | reads Memory context |
| `app/gateway/routers/memory.py` | current HTTP API |
| `config/memory_config.py` | shared configuration |

Change these shared files only when the host-wide Memory contract itself changes.

## Verification

At minimum, verify:

- configuration parsing;
- manager creation;
- required method signatures;
- current Gateway response shape;
- user and employee isolation;
- import/export and fact operations that the backend claims to support;
- context length limits and Secret handling.