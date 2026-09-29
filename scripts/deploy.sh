#!/bin/sh
# Set H3_CONTROLLER to the native packaged executable. Never pass passwords.
set -eu
exec "${H3_CONTROLLER:-h3-controller}" deploy "$@"
