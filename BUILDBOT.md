# Buildbot Worker Container Setup

This repository contains a Podman-based buildbot-worker container configuration.

## Building the Container

To build the container with Podman:

```bash
podman build -t buildbot-worker -f Containerfile .
```

## Running the Container

The container includes a helpful entrypoint script. Running without arguments shows usage:

```bash
podman run -it --rm buildbot-worker
```

To start a buildbot-worker:

```bash
podman run -it --rm \
  -v ./worker:/buildarea \
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

The `/buildarea` directory is intentionally set with 0777 permissions to avoid permission-related issues when running builds. This allows any user to read and write to this directory.

**Important Considerations:**
- This configuration prioritizes ease of use over strict security for the build area
- The 0777 permissions mean the directory is world-readable and world-writable within the container
- If the container is compromised, this could be exploited
- When mounting volumes from the host, ensure the host system's security policies are appropriate
- For production use, consider if more restrictive permissions with proper user/group mapping would be suitable for your threat model

This configuration is designed for development and CI/CD environments where ease of configuration is prioritized.
