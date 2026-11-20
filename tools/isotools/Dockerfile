# TVWallE build tools for tools/build-installer.sh: xorriso to repack the
# Ubuntu ISO, openssl and python3 to make the password hashes, PyYAML to check
# the generated autoinstall file. Nothing is installed on the Mac itself.
FROM ubuntu:26.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends xorriso openssl python3 python3-yaml \
 && rm -rf /var/lib/apt/lists/*
