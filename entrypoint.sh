#!/bin/bash
set -e

# If no arguments are provided, show help
if [ $# -eq 0 ]; then
    echo "Buildbot Worker Container"
    echo ""
    echo "Usage examples:"
    echo "  # Create a new worker"
    echo "  podman run -v ./worker:/buildarea buildbot-worker create-worker /buildarea <master>:<port> <name> <password>"
    echo ""
    echo "  # Start an existing worker"
    echo "  podman run -v ./worker:/buildarea buildbot-worker start /buildarea"
    echo ""
    echo "  # Run buildbot-worker commands directly"
    echo "  podman run buildbot-worker buildbot-worker --help"
    exit 0
fi

# Execute the provided command
exec "$@"
