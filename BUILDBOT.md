# Buildbot Worker Container Setup

This repository contains a Podman-based buildbot-worker container configuration.

## Building the Container

To build the container with Podman:

```bash
podman build -t buildbot-worker -f Containerfile .
```

## Running the Container

To run the buildbot-worker:

```bash
podman run -it --rm \
  -v /path/to/worker/config:/home/buildbot/.buildbot \
  buildbot-worker buildbot-worker start /buildarea
```

## Key Features

- **Podman-focused**: Designed specifically for Podman (no Docker support)
- **Dedicated User**: Creates a `buildbot` user with a proper home directory at `/home/buildbot`
- **Build Area**: The `/buildarea` directory is created with world-readable and world-writable permissions (0777) to handle permission issues
- **Python 3.13**: Based on Python 3.13 slim image with buildbot-worker installed

## Directory Structure

- `/home/buildbot` - Home directory for the buildbot user
- `/buildarea` - Working directory with 0777 permissions for build operations

## Configuration

The buildbot-worker can be configured by:
1. Mounting a volume with buildbot configuration to `/home/buildbot/.buildbot`
2. Setting environment variables for buildbot master connection
3. Using `buildbot-worker create-worker` command to initialize the worker

Example initialization:
```bash
podman run -it --rm \
  -v ./buildbot-worker:/buildarea \
  buildbot-worker \
  buildbot-worker create-worker /buildarea <master-host>:<port> <worker-name> <password>
```

## Security Note

The `/buildarea` directory is set with 0777 permissions to avoid permission-related issues when running builds. This is acceptable in a container context where the buildbot user is isolated within the container.
