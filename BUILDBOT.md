# Buildbot Worker Setup with Podman

This repository uses Podman (not Docker) for running buildbot-worker.

## Building the Container

```bash
podman build -t buildbot-worker -f Containerfile .
```

## Running the Container

```bash
podman run -d --name buildbot-worker \
  -v /path/to/worker/config:/buildarea \
  buildbot-worker
```

## Configuration Details

- **User**: The container runs as the `buildbot` user (non-root)
- **Home Directory**: `/home/buildbot` - properly configured with user permissions
- **Build Area**: `/buildarea` - world readable and writable (0777 permissions)
- **Python Version**: 3.13

## Permissions

The buildarea directory is created with world readable and writable permissions (0777) to avoid permission issues when mounting volumes or running with different user contexts.
